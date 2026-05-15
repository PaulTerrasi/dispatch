"""Claude Agent SDK wiring: tools, options, and a thin AgentRunner protocol.

The runner depends on `AgentRunner`, not on the SDK directly. Tests inject a
`ScriptedAgentRunner` that drives our own tools deterministically; production
uses `SdkAgentRunner` which delegates to `claude_agent_sdk.query`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

import structlog
from claude_agent_sdk import (
    ClaudeAgentOptions,
    SdkMcpTool,
    create_sdk_mcp_server,
    query,
    tool,
)

from digest.patch import PatchError, apply_unified_diff
from digest.store_protocol import StoreProtocol
from digest.tools.rss import fetch_rss
from digest.tools.web_fetch import web_fetch
from digest.tools.youtube import fetch_youtube_channel, fetch_youtube_transcript

log = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


# ---------- Run state -------------------------------------------------------


@dataclass
class RunState:
    """Mutable state shared with the SDK tools via closure.

    Holds Store, today's date, and the eventual digest payload + reflection
    notes. Tools mutate this; the runner reads from it after the agent ends.

    `current_tools` is populated by each build_*_tools() call so the
    ScriptedAgentRunner (used in tests) can resolve tool names without
    introspecting the SDK's MCP server config.
    """

    store: StoreProtocol
    today: date
    run_id: str = ""
    submitted_items: list[dict[str, Any]] | None = None
    agent_notes: str = ""
    reflection_notes: str = ""
    profile_patches_applied: int = 0
    sources_changed: int = 0
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    current_tools: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = field(
        default_factory=dict
    )
    # Set by options builders; persisted to digest JSON for display in the runs UI.
    curation_system_prompt: str = ""
    reflection_system_prompt: str = ""
    # Accumulated thinking text from the most recent AssistantMessage; consumed by record().
    pending_thinking: str | None = None
    # Reflection-only: the feedback event that triggered this run, if any.
    triggering_event: dict[str, Any] | None = None

    def record(
        self,
        name: str,
        args: dict[str, Any],
        outcome: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        thinking = self.pending_thinking
        self.pending_thinking = None
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "tool": name,
            "args": {k: v for k, v in args.items() if k != "html"},
            "outcome": outcome,
        }
        if thinking:
            entry["thinking"] = thinking
        if extra:
            entry.update(extra)
        self.tool_log.append(entry)


# ---------- Tool definitions ------------------------------------------------


def _stable_id(url: str, title: str) -> str:
    h = hashlib.sha1(f"{url}|{title}".encode()).hexdigest()
    return h[:8]


def build_curation_tools(state: RunState) -> list[SdkMcpTool[Any]]:
    """Returns SDK MCP tools for the curation phase.

    RunState side-effects (closure-captured `state`):
      - every tool: appends an entry to `state.tool_log` via `state.record()`.
      - submit_digest: writes `state.submitted_items` and `state.agent_notes`.
    No other tool here mutates state. The runner reads these fields after the
    agent stream ends.
    """

    @tool(
        "read_profile",
        "Read the user's profile markdown — their standing interests, "
        "things-explored, dislikes, and voice notes.",
        {},
    )
    async def _read_profile(_args: dict[str, Any]) -> dict[str, Any]:
        text = state.store.read_profile()
        # Capture the snapshot on curation runs so a later reflection agent can
        # see exactly which profile produced an item it's reacting to.
        state.record(
            "read_profile",
            {},
            f"{len(text)} chars",
            extra={"profile_snapshot": text},
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "read_recent_feedback",
        "Read user feedback events from the last N days as JSONL.",
        {"days": int},
    )
    async def _read_recent_feedback(args: dict[str, Any]) -> dict[str, Any]:
        days = int(args.get("days", 30))
        events = state.store.read_recent_feedback(days=days)
        text = "\n".join(json.dumps(e) for e in events) or "(no feedback yet)"
        state.record("read_recent_feedback", {"days": days}, f"{len(events)} events")
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "read_recent_digests",
        "Read recent digest items as date, item_id, title, source, url (tab-separated). "
        "Use for deduplication and source provenance.",
        {"days": int},
    )
    async def _read_recent_digests(args: dict[str, Any]) -> dict[str, Any]:
        days = int(args.get("days", 7))
        items = state.store.recent_digest_items(days=days)
        text = (
            "\n".join(
                f"{i['date']}\t{i['id']}\t{i['title']}\t{i['source']}\t{i['url']}" for i in items
            )
            or "(none)"
        )
        state.record("read_recent_digests", {"days": days}, f"{len(items)} items")
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "list_sources",
        "List the user's configured RSS feeds, YouTube channels, and sites.",
        {},
    )
    async def _list_sources(_args: dict[str, Any]) -> dict[str, Any]:
        sources = state.store.list_sources()
        text = (
            "\n".join(f"{s.kind}\t{s.value}\t{s.name or ''}\t{','.join(s.tags)}" for s in sources)
            or "(no sources configured)"
        )
        state.record("list_sources", {}, f"{len(sources)} sources")
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "fetch_rss",
        "Fetch and parse an RSS or Atom feed; returns recent entries.",
        {"url": str, "limit": int},
    )
    async def _fetch_rss(args: dict[str, Any]) -> dict[str, Any]:
        url = str(args["url"])
        limit = int(args.get("limit", 25))
        try:
            entries = await fetch_rss(url, limit=limit)
            payload = [e.as_dict() for e in entries]
            state.record("fetch_rss", {"url": url}, f"{len(payload)} entries")
            return {"content": [{"type": "text", "text": json.dumps(payload)}]}
        except Exception as e:
            log.exception("tool.fetch_rss_failed", url=url)
            state.record("fetch_rss", {"url": url}, f"error: {e}")
            return {
                "content": [{"type": "text", "text": f"error fetching {url}: {e}"}],
                "isError": True,
            }

    @tool(
        "fetch_youtube_channel",
        "Recent uploads for a YouTube channel (via channel RSS).",
        {"channel_id": str, "limit": int},
    )
    async def _fetch_yt_channel(args: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(args["channel_id"])
        limit = int(args.get("limit", 15))
        try:
            entries = await fetch_youtube_channel(channel_id, limit=limit)
            payload = [e.as_dict() for e in entries]
            state.record("fetch_youtube_channel", {"channel_id": channel_id}, f"{len(payload)}")
            return {"content": [{"type": "text", "text": json.dumps(payload)}]}
        except Exception as e:
            log.exception("tool.fetch_youtube_channel_failed", channel_id=channel_id)
            state.record("fetch_youtube_channel", {"channel_id": channel_id}, f"error: {e}")
            return {
                "content": [{"type": "text", "text": f"error: {e}"}],
                "isError": True,
            }

    @tool(
        "fetch_youtube_transcript",
        "Fetch the transcript text for a YouTube video by its 11-char ID.",
        {"video_id": str},
    )
    async def _fetch_yt_transcript(args: dict[str, Any]) -> dict[str, Any]:
        video_id = str(args["video_id"])
        try:
            t = await fetch_youtube_transcript(video_id)
            state.record("fetch_youtube_transcript", {"video_id": video_id}, f"{len(t.text)} chars")
            return {"content": [{"type": "text", "text": t.text}]}
        except Exception as e:
            log.exception("tool.fetch_youtube_transcript_failed", video_id=video_id)
            state.record("fetch_youtube_transcript", {"video_id": video_id}, f"error: {e}")
            return {
                "content": [{"type": "text", "text": f"transcript unavailable: {e}"}],
                "isError": True,
            }

    @tool(
        "web_fetch",
        "Fetch a URL and return a readability-extracted main text and title.",
        {"url": str},
    )
    async def _web_fetch(args: dict[str, Any]) -> dict[str, Any]:
        url = str(args["url"])
        try:
            doc = await web_fetch(url)
            payload = {"url": doc.url, "title": doc.title, "text": doc.text[:20_000]}
            state.record("web_fetch", {"url": url}, f"{len(doc.text)} chars")
            return {"content": [{"type": "text", "text": json.dumps(payload)}]}
        except Exception as e:
            log.exception("tool.web_fetch_failed", url=url)
            state.record("web_fetch", {"url": url}, f"error: {e}")
            return {
                "content": [{"type": "text", "text": f"error fetching {url}: {e}"}],
                "isError": True,
            }

    @tool(
        "submit_digest",
        "Submit today's curated digest. Items: array of "
        "{type, title, source, url, summary, duration_min?}. "
        "agent_notes: one short paragraph on what you considered and cut.",
        {"items": list, "agent_notes": str},
    )
    async def _submit_digest(args: dict[str, Any]) -> dict[str, Any]:
        items_in = args.get("items") or []
        notes = str(args.get("agent_notes") or "")
        if not isinstance(items_in, list):
            return {
                "content": [{"type": "text", "text": "items must be an array"}],
                "isError": True,
            }
        items_out: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw in items_in:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            title = str(raw.get("title") or "").strip()
            if not url or not title:
                continue
            iid = _stable_id(url, title)
            if iid in seen_ids:
                continue
            seen_ids.add(iid)
            item: dict[str, Any] = {
                "id": iid,
                "type": raw.get("type") or "article",
                "title": title,
                "source": str(raw.get("source") or ""),
                "url": url,
                "summary": str(raw.get("summary") or "").strip(),
                "feedback": None,
                "run_id": state.run_id,
            }
            if "duration_min" in raw and raw["duration_min"] is not None:
                try:
                    item["duration_min"] = int(raw["duration_min"])
                except (TypeError, ValueError):
                    pass
            items_out.append(item)
        state.submitted_items = items_out
        state.agent_notes = notes
        state.record("submit_digest", {"count": len(items_out)}, "ok")
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"submitted {len(items_out)} items; curation phase complete.",
                }
            ]
        }

    tools = [
        _read_profile,
        _read_recent_feedback,
        _read_recent_digests,
        _list_sources,
        _fetch_rss,
        _fetch_yt_channel,
        _fetch_yt_transcript,
        _web_fetch,
        _submit_digest,
    ]
    state.current_tools = {t.name: t.handler for t in tools}
    return tools


def build_reflection_tools(state: RunState) -> list[SdkMcpTool[Any]]:
    """Returns SDK MCP tools for the reflection phase.

    RunState side-effects (closure-captured `state`):
      - every tool: appends to `state.tool_log` via `state.record()`.
      - patch_profile: increments `state.profile_patches_applied` and writes
        `state.store.write_profile()` if the diff applies cleanly.
      - add_source / remove_source: increments `state.sources_changed` and
        writes `state.store.write_sources()`.
      - end_reflection: writes `state.reflection_notes`.
    """

    @tool(
        "read_profile",
        "Read the user's profile markdown.",
        {},
    )
    async def _read_profile(_args: dict[str, Any]) -> dict[str, Any]:
        text = state.store.read_profile()
        state.record("read_profile", {}, f"{len(text)} chars")
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "read_recent_feedback",
        "Read user feedback events from the last N days. Filtered to events "
        "strictly before the triggering event, plus the triggering event "
        "itself appended last and labeled — so you can never confuse later "
        "queued feedback for context that informed this run.",
        {"days": int},
    )
    async def _read_recent_feedback(args: dict[str, Any]) -> dict[str, Any]:
        days = int(args.get("days", 14))
        events = state.store.read_recent_feedback(days=days)
        trigger = state.triggering_event
        assert isinstance(trigger, dict), "reflection requires a triggering_event"
        trigger_ts = trigger.get("ts")
        assert isinstance(trigger_ts, str), "triggering_event must have a ts"
        prior = [e for e in events if isinstance(e.get("ts"), str) and e["ts"] < trigger_ts]
        lines = [json.dumps(e) for e in prior]
        lines.append("# --- triggering event ---")
        lines.append(json.dumps(trigger))
        text = "\n".join(lines)
        outcome = f"{len(prior)} prior events + 1 trigger"
        state.record("read_recent_feedback", {"days": days}, outcome)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "read_recent_digests",
        "Read recent digest items as date, item_id, title, source, url (tab-separated). "
        "Use to correlate feedback item_ids with source domains.",
        {"days": int},
    )
    async def _read_recent_digests(args: dict[str, Any]) -> dict[str, Any]:
        days = int(args.get("days", 1))
        items = state.store.recent_digest_items(days=days)
        text = (
            "\n".join(
                f"{i['date']}\t{i['id']}\t{i['title']}\t{i['source']}\t{i['url']}" for i in items
            )
            or "(none)"
        )
        state.record("read_recent_digests", {"days": days}, f"{len(items)} items")
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "list_sources",
        "List configured RSS feeds, YouTube channels, and sites from sources.yaml.",
        {},
    )
    async def _list_sources(_args: dict[str, Any]) -> dict[str, Any]:
        sources = state.store.list_sources()
        text = (
            "\n".join(f"{s.kind}\t{s.value}\t{s.name or ''}\t{','.join(s.tags)}" for s in sources)
            or "(no sources configured)"
        )
        state.record("list_sources", {}, f"{len(sources)} sources")
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "add_source",
        "Add a new source to sources.yaml. kind must be 'rss', 'youtube', or 'site'. "
        "value is the feed URL, YouTube channel_id, or site URL. "
        "Call list_sources first to confirm it is not already present.",
        {"kind": str, "value": str, "name": str, "tags": list},
    )
    async def _add_source(args: dict[str, Any]) -> dict[str, Any]:
        kind = str(args.get("kind") or "").strip()
        value = str(args.get("value") or "").strip()
        name = str(args.get("name") or "").strip() or None
        raw_tags = args.get("tags") or []
        tags = [str(t) for t in raw_tags if t] if isinstance(raw_tags, list) else []
        if kind not in ("rss", "youtube", "site"):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"invalid kind {kind!r}; must be rss, youtube, or site",
                    }
                ],
                "isError": True,
            }
        if not value:
            return {"content": [{"type": "text", "text": "value is required"}], "isError": True}
        sources = state.store.list_sources()
        if any(s.kind == kind and s.value == value for s in sources):
            return {
                "content": [
                    {"type": "text", "text": f"{kind} source {value!r} is already in sources.yaml"}
                ],
                "isError": True,
            }
        from digest.store import Source as _Source

        state.store.write_sources([*sources, _Source(kind=kind, value=value, name=name, tags=tags)])
        state.sources_changed += 1
        state.record("add_source", {"kind": kind, "value": value}, "added")
        return {"content": [{"type": "text", "text": f"added {kind} source: {value}"}]}

    @tool(
        "remove_source",
        "Remove a source from sources.yaml by kind and value. "
        "Only remove sources that have been consistently disliked or quiet for 3+ weeks.",
        {"kind": str, "value": str},
    )
    async def _remove_source(args: dict[str, Any]) -> dict[str, Any]:
        kind = str(args.get("kind") or "").strip()
        value = str(args.get("value") or "").strip()
        sources = state.store.list_sources()
        filtered = [s for s in sources if not (s.kind == kind and s.value == value)]
        if len(filtered) == len(sources):
            return {
                "content": [
                    {"type": "text", "text": f"{kind} source {value!r} not found in sources.yaml"}
                ],
                "isError": True,
            }
        state.store.write_sources(filtered)
        state.sources_changed += 1
        state.record("remove_source", {"kind": kind, "value": value}, "removed")
        return {"content": [{"type": "text", "text": f"removed {kind} source: {value}"}]}

    @tool(
        "patch_profile",
        "Apply a unified diff to profile.md. Returns an error message if the "
        "diff doesn't apply cleanly so you can revise and retry.",
        {"diff": str},
    )
    async def _patch_profile(args: dict[str, Any]) -> dict[str, Any]:
        diff = str(args.get("diff") or "")
        original = state.store.read_profile()
        try:
            patched = apply_unified_diff(original, diff)
        except PatchError as e:
            state.record("patch_profile", {}, f"rejected: {e}")
            return {
                "content": [{"type": "text", "text": f"patch rejected: {e}"}],
                "isError": True,
            }
        state.store.write_profile(patched)
        state.profile_patches_applied += 1
        state.record("patch_profile", {}, "applied")
        return {"content": [{"type": "text", "text": "profile.md updated."}]}

    @tool(
        "read_recent_curation_runs",
        "Read recent curation runs (the agent's tool_log + items submitted) "
        "so you can see WHY a thumbs-down item was picked, or which profile "
        "line led the agent to a thumbs-up. Trace feedback back to the source "
        "of the selection rather than guessing.",
        {"days": int},
    )
    async def _read_recent_curation_runs(args: dict[str, Any]) -> dict[str, Any]:
        days = int(args.get("days", 7))
        runs = [r for r in state.store.read_recent_runs(days=days) if r.get("kind") == "curation"]
        text = _compact_curation_runs(runs, max_bytes=40_000)
        state.record(
            "read_recent_curation_runs",
            {"days": days},
            f"{len(runs)} runs",
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "read_triggering_curation_run",
        "For thumb feedback: return the specific curation run that surfaced "
        "the item being reacted to, PLUS the profile snapshot active at that "
        "run. The snapshot — not the live read_profile — is what produced "
        "this item; anchor any profile edit against it.",
        {},
    )
    async def _read_triggering_curation_run(_args: dict[str, Any]) -> dict[str, Any]:
        trigger = state.triggering_event
        item_id = trigger.get("item_id") if isinstance(trigger, dict) else None
        if not item_id:
            text = (
                "(triggering event has no item_id; use read_recent_curation_runs "
                "for chat-feedback context)"
            )
            state.record("read_triggering_curation_run", {}, "no item_id")
            return {"content": [{"type": "text", "text": text}]}
        runs = [r for r in state.store.read_recent_runs(days=14) if r.get("kind") == "curation"]
        match: dict[str, Any] | None = None
        for r in runs:
            ids = r.get("submitted_item_ids")
            if isinstance(ids, list) and item_id in ids:
                match = r
                break
            # Legacy runs (before submitted_item_ids was added) can't be
            # matched: the recorded args carry only a "count", not the item
            # ids, and we don't have a digest-date → item-id reverse index
            # here. Skip them and fall through to the not-found path.
        if match is None:
            text = f"(no curation run in the last 14 days surfaced item_id={item_id!r})"
            state.record("read_triggering_curation_run", {"item_id": item_id}, "not found")
            return {"content": [{"type": "text", "text": text}]}
        snapshot: str | None = None
        for entry in match.get("tool_log") or []:
            if entry.get("tool") == "read_profile" and isinstance(
                entry.get("profile_snapshot"), str
            ):
                snapshot = entry["profile_snapshot"]
                break
        compact = _compact_curation_runs([match], max_bytes=40_000)
        if snapshot is None:
            tail = (
                "\n## Profile snapshot at curation time\n"
                "(profile snapshot unavailable for this run — pre-dates snapshot capture)\n"
            )
        else:
            tail = (
                "\n## Profile snapshot at curation time\n"
                "(This is the profile that produced the item — NOT necessarily the current "
                "profile. Anchor any edit to this text.)\n\n"
                f"{snapshot}\n"
            )
        text = compact + tail
        state.record(
            "read_triggering_curation_run",
            {"item_id": item_id},
            f"matched run {match.get('run_id', '?')}",
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "read_reflection_memory",
        "Read the rolling reflection journal — your only carryover state "
        "between reflection runs. Notes from past reflection agents about "
        "trends, deferred patterns, and things to watch.",
        {},
    )
    async def _read_reflection_memory(_args: dict[str, Any]) -> dict[str, Any]:
        text = state.store.read_reflection_memory()
        state.record("read_reflection_memory", {}, f"{len(text)} chars")
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "write_reflection_memory",
        "Overwrite the reflection journal. Aim for ~700 words; hard cap "
        "5000 chars (writes beyond that are rejected so you re-summarize). "
        "Re-summarize rather than appending forever.",
        {"text": str},
    )
    async def _write_reflection_memory(args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text") or "")
        if len(text) > 5000:
            state.record("write_reflection_memory", {}, f"rejected: {len(text)} chars > 5000")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"rejected: {len(text)} chars exceeds the 5000-char cap. "
                            "Re-summarize and try again."
                        ),
                    }
                ],
                "isError": True,
            }
        state.store.write_reflection_memory(text)
        state.record("write_reflection_memory", {}, f"{len(text)} chars written")
        return {"content": [{"type": "text", "text": "reflection memory updated."}]}

    @tool(
        "end_reflection",
        "End the reflection phase. Pass a short note describing what changed "
        "(or that nothing changed and why).",
        {"notes": str},
    )
    async def _end_reflection(args: dict[str, Any]) -> dict[str, Any]:
        state.reflection_notes = str(args.get("notes") or "")
        state.record("end_reflection", {}, "ok")
        return {"content": [{"type": "text", "text": "reflection complete."}]}

    tools = [
        _read_profile,
        _read_recent_feedback,
        _read_recent_digests,
        _read_recent_curation_runs,
        _read_triggering_curation_run,
        _read_reflection_memory,
        _write_reflection_memory,
        _list_sources,
        _add_source,
        _remove_source,
        _patch_profile,
        _end_reflection,
    ]
    state.current_tools = {t.name: t.handler for t in tools}
    return tools


def _compact_curation_runs(runs: list[dict[str, Any]], *, max_bytes: int) -> str:
    """Newest-first compact rendering of curation runs for the reflection agent.

    Drops verbose `args.html` / `args.text` (already stripped by RunState.record
    for `html`, but we belt-and-suspender here). Stops appending blocks once the
    byte budget is reached so older runs are truncated, not the most relevant
    recent ones.
    """
    if not runs:
        return "(no curation runs in window)"
    blocks: list[str] = []
    used = 0
    for r in runs:
        lines = [
            f"## run {r.get('run_id', '?')} @ {r.get('started_at', '?')}",
            f"items: {r.get('item_count', 0)}",
        ]
        for entry in r.get("tool_log", []) or []:
            tool_name = entry.get("tool", "?")
            args_dict = {
                k: v
                for k, v in (entry.get("args") or {}).items()
                if k not in ("html", "text") and not (isinstance(v, str) and len(v) > 200)
            }
            outcome = entry.get("outcome", "")
            line = f"- {tool_name}({json.dumps(args_dict, default=str)}) -> {outcome}"
            thinking = entry.get("thinking")
            if thinking:
                trimmed = thinking.strip().replace("\n", " ")
                if len(trimmed) > 200:
                    trimmed = trimmed[:200] + "…"
                line += f"\n  thinking: {trimmed}"
            lines.append(line)
        block = "\n".join(lines) + "\n"
        if used + len(block) > max_bytes:
            blocks.append(f"... ({len(runs) - len(blocks)} older runs truncated)")
            break
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks)


# ---------- AgentRunner protocol & impls ------------------------------------


class AgentRunner(Protocol):
    # Note: declared with `def` (not `async def`) so this Protocol matches
    # async-generator implementations, which return AsyncIterator at the type
    # level rather than Coroutine[..., AsyncIterator].
    def run(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]: ...


class SdkAgentRunner:
    """Thin wrapper over `claude_agent_sdk.query`."""

    async def run(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        async for msg in query(prompt=prompt, options=options):
            yield msg


@dataclass
class ScriptedAgentRunner:
    """Test double: invokes our tool functions in a hardcoded sequence.

    The script is a list of (tool_name, args) pairs. The runner reads tool
    callables from `state.current_tools`, which is populated by the
    `build_*_tools()` calls inside `curation_options`/`reflection_options`.
    """

    state: RunState
    script: list[tuple[str, dict[str, Any]]]

    async def run(self, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Any]:
        del prompt, options  # unused — script is deterministic
        for name, args in self.script:
            handler = self.state.current_tools.get(name)
            if handler is None:
                raise RuntimeError(
                    f"scripted tool {name!r} not registered for current phase "
                    f"(have: {sorted(self.state.current_tools)})"
                )
            await handler(args)
        # Mimic an end-of-stream marker; runner doesn't depend on shape.
        yield {"type": "result", "scripted": True}


# ---------- Options builders ------------------------------------------------


def _system_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def curation_options(state: RunState, *, max_turns: int = 40) -> ClaudeAgentOptions:
    system = _system_prompt("system.md")
    state.curation_system_prompt = system
    server = create_sdk_mcp_server(
        name="digest",
        version="0.1.0",
        tools=build_curation_tools(state),
    )
    return ClaudeAgentOptions(
        system_prompt=system,
        mcp_servers={"digest": server},
        allowed_tools=[
            "mcp__digest__read_profile",
            "mcp__digest__read_recent_feedback",
            "mcp__digest__read_recent_digests",
            "mcp__digest__list_sources",
            "mcp__digest__fetch_rss",
            "mcp__digest__fetch_youtube_channel",
            "mcp__digest__fetch_youtube_transcript",
            "mcp__digest__web_fetch",
            "mcp__digest__submit_digest",
            "WebSearch",
            "WebFetch",
        ],
        max_turns=max_turns,
        permission_mode="bypassPermissions",
        thinking={"type": "adaptive"},
    )


def reflection_options(state: RunState, *, max_turns: int = 20) -> ClaudeAgentOptions:
    system = _system_prompt("reflection.md")
    state.reflection_system_prompt = system
    server = create_sdk_mcp_server(
        name="digest",
        version="0.1.0",
        tools=build_reflection_tools(state),
    )
    return ClaudeAgentOptions(
        system_prompt=system,
        mcp_servers={"digest": server},
        allowed_tools=[
            "mcp__digest__read_profile",
            "mcp__digest__read_recent_feedback",
            "mcp__digest__read_recent_digests",
            "mcp__digest__read_recent_curation_runs",
            "mcp__digest__read_triggering_curation_run",
            "mcp__digest__read_reflection_memory",
            "mcp__digest__write_reflection_memory",
            "mcp__digest__list_sources",
            "mcp__digest__add_source",
            "mcp__digest__remove_source",
            "mcp__digest__patch_profile",
            "mcp__digest__end_reflection",
        ],
        max_turns=max_turns,
        permission_mode="bypassPermissions",
        thinking={"type": "adaptive"},
    )


def build_chat_tools(
    state: RunState, profile_changed_q: asyncio.Queue[str]
) -> list[SdkMcpTool[Any]]:
    """Chat-phase tools: same surface as reflection, but each successful write
    pushes the tool name onto `profile_changed_q` so the SSE generator can tell
    the PWA to refetch profile.md / sources.yaml. Writes also serialize against
    the hourly reflection job by acquiring `try_acquire_reflection_lock` for the
    duration of the call.
    """
    base = build_reflection_tools(state)
    by_name = {t.name: t for t in base}
    write_tools = {"patch_profile", "add_source", "remove_source"}

    def _wrap_write(orig: SdkMcpTool[Any]) -> SdkMcpTool[Any]:
        original_handler = orig.handler

        async def wrapped(args: dict[str, Any]) -> dict[str, Any]:
            token = state.store.try_acquire_reflection_lock(ttl_seconds=60)
            if token is None:
                state.record(orig.name, args, "rejected: reflection lock held")
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "The hourly reflection job is currently editing the "
                                "profile. Try again in a minute."
                            ),
                        }
                    ],
                    "isError": True,
                }
            try:
                result = await original_handler(args)
            finally:
                state.store.release_reflection_lock(token)
            if not result.get("isError"):
                profile_changed_q.put_nowait(orig.name)
            return result

        return SdkMcpTool(
            name=orig.name,
            description=orig.description,
            input_schema=orig.input_schema,
            handler=wrapped,
            annotations=orig.annotations,
        )

    tools = [_wrap_write(by_name[n]) if n in write_tools else by_name[n] for n in by_name]
    state.current_tools = {t.name: t.handler for t in tools}
    return tools


def chat_options(
    state: RunState,
    profile_changed_q: asyncio.Queue[str],
    *,
    max_turns: int = 8,
) -> ClaudeAgentOptions:
    """Options for the conversational 'chat' agent driven from the PWA.

    Reuses the reflection toolkit but with `include_partial_messages=True` so
    the SDK yields `StreamEvent` deltas we can forward over SSE for live token
    streaming. `max_turns` is small to keep each turn snappy and prevent the
    agent from looping over tools.
    """
    system = _system_prompt("chat.md")
    server = create_sdk_mcp_server(
        name="digest",
        version="0.1.0",
        tools=build_chat_tools(state, profile_changed_q),
    )
    return ClaudeAgentOptions(
        system_prompt=system,
        mcp_servers={"digest": server},
        allowed_tools=[
            "mcp__digest__read_profile",
            "mcp__digest__read_recent_feedback",
            "mcp__digest__read_recent_digests",
            "mcp__digest__read_recent_curation_runs",
            "mcp__digest__read_triggering_curation_run",
            "mcp__digest__read_reflection_memory",
            "mcp__digest__write_reflection_memory",
            "mcp__digest__list_sources",
            "mcp__digest__add_source",
            "mcp__digest__remove_source",
            "mcp__digest__patch_profile",
            "mcp__digest__end_reflection",
        ],
        max_turns=max_turns,
        permission_mode="bypassPermissions",
        include_partial_messages=True,
    )
