from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from digest.store_protocol import StoreProtocol
from server.deps import get_store

log = structlog.get_logger(__name__)
router = APIRouter()
StoreDep = Annotated[StoreProtocol, Depends(get_store)]


class DigestSummary(BaseModel):
    date: str
    item_count: int
    has_unread: bool


class DigestItem(BaseModel):
    id: str
    type: str
    title: str
    source: str
    url: str
    summary: str
    summary_more: str | None = None
    duration_min: float | None = None
    feedback: str | None = None
    run_id: str | None = None


class Digest(BaseModel):
    date: str
    generated_at: str | None = None
    items: list[DigestItem]
    agent_notes: str = ""
    runs: list[dict[str, Any]] = []


class FeedItem(DigestItem):
    digest_date: str
    run_started_at: str | None = None


def _runs_from_digest(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the list of run entries from a digest dict, handling old and new schema."""
    runs = data.get("runs")
    if runs is not None:
        return list(runs)
    run = data.get("run")
    if run:
        return [dict(run)]
    return []


@router.get("/digest/today", response_model=Digest | None)
def get_today(store: StoreDep) -> Digest | None:
    """Most recent digest. May be yesterday's if today's hasn't run yet."""
    digests = store.list_digests()
    if not digests:
        return None
    data = store.read_digest(digests[0])
    if not data:
        return None
    return Digest(**data)


@router.get("/digest/{day}", response_model=Digest)
def get_by_date(store: StoreDep, day: str, feedback_only: bool = False) -> Digest:
    try:
        d = date.fromisoformat(day)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid date") from e
    data = store.read_digest(d)
    if not data:
        raise HTTPException(status_code=404, detail="no digest for that day")
    if feedback_only:
        data = {
            **data,
            "items": [i for i in data.get("items", []) or [] if i.get("feedback") is not None],
        }
    return Digest(**data)


@router.get("/digests", response_model=list[DigestSummary])
def list_digests(store: StoreDep) -> list[DigestSummary]:
    """Archive index: only digests that contain at least one reacted item.
    `item_count` reflects reacted items only, since unreacted ones live in the feed."""
    out: list[DigestSummary] = []
    for d in store.list_digests():
        data = store.read_digest(d) or {}
        items = data.get("items", []) or []
        reacted = [i for i in items if i.get("feedback") is not None]
        if not reacted:
            continue
        out.append(
            DigestSummary(
                date=d.isoformat(),
                item_count=len(reacted),
                has_unread=False,
            )
        )
    return out


@router.get("/feed", response_model=list[FeedItem])
def get_feed(store: StoreDep) -> list[FeedItem]:
    """Inbox-style feed: every unreacted item across all digests, newest first.
    Once an item is thumbed up/down it leaves the feed and only appears in the archive."""
    out: list[FeedItem] = []
    for d in store.list_digests():
        data = store.read_digest(d) or {}
        runs = _runs_from_digest(data)
        run_times = {r.get("run_id"): r.get("started_at") for r in runs if r.get("run_id")}
        for item in data.get("items", []) or []:
            if item.get("feedback") is None:
                rsa = run_times.get(item.get("run_id"))
                out.append(FeedItem(**item, digest_date=d.isoformat(), run_started_at=rsa))
    return out


class SearchHit(BaseModel):
    date: str
    item_id: str
    title: str
    url: str
    snippet: str


class ToolCallEntry(BaseModel):
    ts: str
    tool: str
    args: dict[str, Any]
    outcome: str
    thinking: str | None = None
    details: dict[str, Any] | None = None


class RunSummaryOut(BaseModel):
    date: str
    run_id: str | None = None
    kind: Literal["curation", "reflection"] = "curation"
    started_at: str | None = None
    duration_seconds: float | None = None
    tool_calls: int | None = None
    profile_patches: int = 0
    sources_changed: int = 0
    reflection_notes: str = ""
    item_count: int | None = 0
    triggering_feedback: dict[str, Any] | None = None
    exit_reason: str | None = None
    error: str | None = None


class RunDetail(RunSummaryOut):
    tool_log: list[ToolCallEntry] = []
    agent_notes: str = ""
    items: list[DigestItem] = []
    system_prompt: str = ""
    user_prompt: str = ""


@router.get("/search", response_model=list[SearchHit])
def search(store: StoreDep, q: str) -> list[SearchHit]:
    """Naive substring search across all digests' titles + summaries.
    Single-user app, so no need for a real index."""
    if not q.strip():
        return []
    needle = q.strip().lower()
    out: list[SearchHit] = []
    for d in store.list_digests():
        data = store.read_digest(d) or {}
        for item in data.get("items", []) or []:
            blob = " ".join(
                [
                    item.get("title", "") or "",
                    item.get("summary", "") or "",
                    item.get("summary_more", "") or "",
                    item.get("source", "") or "",
                ]
            ).lower()
            if needle in blob:
                snippet = item.get("summary", "")[:160]
                out.append(
                    SearchHit(
                        date=d.isoformat(),
                        item_id=item.get("id", ""),
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=snippet,
                    )
                )
    return out[:200]


@router.get("/_health")
def health() -> dict[str, str]:
    return {"status": "ok", "now": datetime.now(UTC).isoformat()}


def _run_date(run: dict[str, Any], fallback: str) -> str:
    ts = run.get("started_at")
    if isinstance(ts, str) and len(ts) >= 10:
        return ts[:10]
    return fallback


def _legacy_run_to_record(run: dict[str, Any], digest_date: str, item_count: int) -> dict[str, Any]:
    """Reshape a legacy embedded-in-digest run dict into the new flat schema."""
    return {
        "run_id": run.get("run_id"),
        "kind": "curation",
        "started_at": run.get("started_at"),
        "duration_seconds": run.get("duration_seconds"),
        "tool_calls": run.get("tool_calls"),
        "tool_log": run.get("tool_log") or [],
        "profile_patches": run.get("profile_patches", 0),
        "sources_changed": run.get("sources_changed", 0),
        "reflection_notes": run.get("reflection_notes", ""),
        "item_count": item_count,
        "system_prompt": run.get("curation_system_prompt", ""),
        "user_prompt": run.get("curation_user_prompt", ""),
        "_digest_date": digest_date,
    }


def _all_runs(store: StoreProtocol, *, days: int) -> list[dict[str, Any]]:
    """Merge runs from the new top-level runs/ store with legacy digest['runs'].
    De-dupes by run_id (new-store wins). Sorted newest-first by started_at."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in store.read_recent_runs(days=days):
        rid = r.get("run_id")
        if isinstance(rid, str):
            seen.add(rid)
        out.append(r)
    for d in store.list_digests():
        data = store.read_digest(d) or {}
        items = data.get("items") or []
        counts: dict[str, int] = {}
        for item in items:
            irid = item.get("run_id")
            if irid:
                counts[irid] = counts.get(irid, 0) + 1
        for run in _runs_from_digest(data):
            rid = run.get("run_id")
            if isinstance(rid, str) and rid in seen:
                continue
            per_run = counts.get(rid, 0) if (rid and counts) else len(items)
            out.append(_legacy_run_to_record(run, d.isoformat(), per_run))
            if isinstance(rid, str):
                seen.add(rid)
    out.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return out


@router.get("/runs", response_model=list[RunSummaryOut])
def list_runs(store: StoreDep) -> list[RunSummaryOut]:
    """All run summaries, newest first (one entry per run). Excludes tool_log."""
    out: list[RunSummaryOut] = []
    for run in _all_runs(store, days=90):
        out.append(
            RunSummaryOut(
                date=_run_date(run, run.get("_digest_date", "")),
                run_id=run.get("run_id"),
                kind=run.get("kind", "curation"),
                started_at=run.get("started_at"),
                duration_seconds=run.get("duration_seconds"),
                tool_calls=run.get("tool_calls"),
                profile_patches=run.get("profile_patches", 0),
                sources_changed=run.get("sources_changed", 0),
                reflection_notes=run.get("reflection_notes", ""),
                item_count=run.get("item_count"),
                triggering_feedback=run.get("triggering_feedback"),
                exit_reason=run.get("exit_reason"),
                error=run.get("error"),
            )
        )
    return out


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(store: StoreDep, run_id: str) -> RunDetail:
    """Full run detail for a specific run_id, including the tool call log."""
    run = store.read_run(run_id)
    digest_date_iso = ""
    items: list[DigestItem] = []
    agent_notes = ""
    if run is None:
        # Fall back to legacy embedded-in-digest runs.
        for d in store.list_digests():
            data = store.read_digest(d) or {}
            for legacy in _runs_from_digest(data):
                if legacy.get("run_id") == run_id:
                    digest_items = data.get("items") or []
                    counts: dict[str, int] = {}
                    for it in digest_items:
                        irid = it.get("run_id")
                        if irid:
                            counts[irid] = counts.get(irid, 0) + 1
                    item_count = counts.get(run_id, 0) if counts else len(digest_items)
                    run = _legacy_run_to_record(legacy, d.isoformat(), item_count)
                    digest_date_iso = d.isoformat()
                    agent_notes = data.get("agent_notes", "")
                    for i in data.get("items") or []:
                        try:
                            items.append(DigestItem(**i))
                        except (TypeError, ValueError) as exc:
                            log.warning(
                                "routes_digest.malformed_digest_item",
                                run_id=run_id,
                                error=str(exc),
                            )
                    break
            if run is not None:
                break
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    if not digest_date_iso:
        digest_date_iso = _run_date(run, run.get("_digest_date", ""))

    tool_log: list[ToolCallEntry] = []
    for e in run.get("tool_log") or []:
        # Legacy runs stored profile_snapshot as a flat entry key; new runs
        # nest all extras under `details`. Surface either to the client, and
        # strip the legacy key so it doesn't ride along as an unknown field.
        if (
            isinstance(e, dict)
            and e.get("tool") == "read_profile"
            and e.get("details") is None
            and isinstance(e.get("profile_snapshot"), str)
        ):
            snapshot = e["profile_snapshot"]
            e = {k: v for k, v in e.items() if k != "profile_snapshot"}
            e["details"] = {"profile_snapshot": snapshot}
        try:
            tool_log.append(ToolCallEntry(**e))
        except (TypeError, ValueError) as exc:
            log.warning(
                "routes_digest.malformed_tool_log_entry",
                run_id=run_id,
                error=str(exc),
            )

    return RunDetail(
        date=digest_date_iso,
        run_id=run.get("run_id"),
        kind=run.get("kind", "curation"),
        started_at=run.get("started_at"),
        duration_seconds=run.get("duration_seconds"),
        tool_calls=run.get("tool_calls"),
        profile_patches=run.get("profile_patches", 0),
        sources_changed=run.get("sources_changed", 0),
        reflection_notes=run.get("reflection_notes", ""),
        item_count=run.get("item_count"),
        triggering_feedback=run.get("triggering_feedback"),
        exit_reason=run.get("exit_reason"),
        error=run.get("error"),
        tool_log=tool_log,
        agent_notes=agent_notes,
        items=items,
        system_prompt=run.get("system_prompt", ""),
        user_prompt=run.get("user_prompt", ""),
    )


@router.get("/_runs/recent")
def recent_runs(store: StoreDep) -> list[dict[str, Any]]:
    """Last 14 days of run summaries (curation + reflection), newest first."""
    out: list[dict[str, Any]] = []
    for run in _all_runs(store, days=14):
        out.append(
            {
                "date": _run_date(run, run.get("_digest_date", "")),
                "run_id": run.get("run_id"),
                "kind": run.get("kind", "curation"),
                "started_at": run.get("started_at"),
                "item_count": run.get("item_count"),
                "duration_seconds": run.get("duration_seconds"),
                "tool_calls": run.get("tool_calls"),
                "profile_patches": run.get("profile_patches", 0),
                "reflection_notes": run.get("reflection_notes", ""),
                "triggering_feedback": run.get("triggering_feedback"),
            }
        )
    return out
