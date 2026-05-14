"""Filesystem-backed store: reads/writes profile.md, sources.yaml, digests/, feedback/.

The store owns *all* IO on the data/ tree. Tools and the runner go through it,
which keeps tests trivial (one tmp_path fixture covers everything).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Source:
    kind: str  # "rss" | "youtube" | "site"
    value: str  # url for rss/site, channel_id for youtube
    name: str | None = None
    tags: list[str] = field(default_factory=list)


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir
        self.profile_path = data_dir / "profile.md"
        self.sources_path = data_dir / "sources.yaml"
        self.reflection_memory_path = data_dir / "reflection_memory.md"
        self.digests_dir = data_dir / "digests"
        self.feedback_dir = data_dir / "feedback"
        self.runs_dir = data_dir / "runs"
        self.state_dir = data_dir / "state"

    def ensure_layout(self) -> None:
        self.digests_dir.mkdir(parents=True, exist_ok=True)
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self.profile_path.exists():
            self.profile_path.write_text(_DEFAULT_PROFILE, encoding="utf-8")
        if not self.sources_path.exists():
            self.sources_path.write_text(_DEFAULT_SOURCES, encoding="utf-8")
        if not self.reflection_memory_path.exists():
            self.reflection_memory_path.write_text(_DEFAULT_REFLECTION_MEMORY, encoding="utf-8")

    # ---- profile ----
    def read_profile(self) -> str:
        return self.profile_path.read_text(encoding="utf-8")

    def write_profile(self, text: str) -> None:
        self.profile_path.write_text(text, encoding="utf-8")

    # ---- reflection memory ----
    # Single-writer by construction: reflection drain holds the reflection lock
    # and the talk phase serializes against it via the same lock.
    def read_reflection_memory(self) -> str:
        if not self.reflection_memory_path.exists():
            return _DEFAULT_REFLECTION_MEMORY
        return self.reflection_memory_path.read_text(encoding="utf-8")

    def write_reflection_memory(self, text: str) -> None:
        self.reflection_memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.reflection_memory_path.write_text(text, encoding="utf-8")

    # ---- sources ----
    def list_sources(self) -> list[Source]:
        if not self.sources_path.exists():
            return []
        raw = yaml.safe_load(self.sources_path.read_text(encoding="utf-8")) or {}
        out: list[Source] = []
        for entry in raw.get("rss", []) or []:
            out.append(Source(kind="rss", value=entry["url"], tags=list(entry.get("tags") or [])))
        for entry in raw.get("youtube", []) or []:
            out.append(
                Source(
                    kind="youtube",
                    value=entry["channel_id"],
                    name=entry.get("name"),
                    tags=list(entry.get("tags") or []),
                )
            )
        for entry in raw.get("sites", []) or []:
            url = entry if isinstance(entry, str) else entry.get("url")
            if url:
                out.append(Source(kind="site", value=url))
        return out

    # ---- digests ----
    def digest_path(self, d: date) -> Path:
        return self.digests_dir / f"{d.isoformat()}.json"

    def write_digest(self, d: date, items: list[dict[str, Any]], agent_notes: str) -> None:
        path = self.digest_path(d)
        if path.exists():
            existing: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            existing_ids = {i.get("id") for i in existing.get("items", []) or []}
            for item in items:
                if item.get("id") not in existing_ids:
                    existing.setdefault("items", []).append(item)
                    existing_ids.add(item.get("id"))
            existing["agent_notes"] = agent_notes
            path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        else:
            payload: dict[str, Any] = {
                "date": d.isoformat(),
                "generated_at": datetime.now(UTC).isoformat(),
                "items": items,
                "agent_notes": agent_notes,
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def rewrite_digest(self, d: date, data: dict[str, Any]) -> None:
        self.digest_path(d).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def read_digest(self, d: date) -> dict[str, Any] | None:
        path = self.digest_path(d)
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def list_digests(self) -> list[date]:
        if not self.digests_dir.exists():
            return []
        out: list[date] = []
        for p in self.digests_dir.glob("*.json"):
            try:
                out.append(date.fromisoformat(p.stem))
            except ValueError:
                continue
        return sorted(out, reverse=True)

    def recent_digest_items(self, days: int = 7) -> list[dict[str, str]]:
        """Returns recent items as {id, title, source, url, date} for dedup and reflection."""
        cutoff = datetime.now(UTC).date() - timedelta(days=days)
        out: list[dict[str, str]] = []
        for d in self.list_digests():
            if d < cutoff:
                continue
            data = self.read_digest(d)
            if not data:
                continue
            for item in data.get("items", []):
                out.append(
                    {
                        "id": item.get("id", ""),
                        "title": item.get("title", ""),
                        "source": item.get("source", ""),
                        "url": item.get("url", ""),
                        "date": d.isoformat(),
                    }
                )
        return out

    def write_sources(self, sources: list[Source]) -> None:
        """Serialize sources back to sources.yaml in the format list_sources() reads."""
        rss_entries: list[dict[str, Any]] = []
        youtube_entries: list[dict[str, Any]] = []
        site_entries: list[str] = []
        for s in sources:
            if s.kind == "rss":
                entry: dict[str, Any] = {"url": s.value}
                if s.tags:
                    entry["tags"] = list(s.tags)
                rss_entries.append(entry)
            elif s.kind == "youtube":
                entry = {"channel_id": s.value}
                if s.name:
                    entry["name"] = s.name
                if s.tags:
                    entry["tags"] = list(s.tags)
                youtube_entries.append(entry)
            elif s.kind == "site":
                site_entries.append(s.value)
        data: dict[str, Any] = {
            "rss": rss_entries,
            "youtube": youtube_entries,
            "sites": site_entries,
        }
        header = "# Sources for the daily digest. Edit freely; the agent reads this each run.\n"
        self.sources_path.write_text(
            header + yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def update_item_feedback(self, d: date, item_id: str, value: str | None) -> bool:
        data = self.read_digest(d)
        if not data:
            return False
        for item in data.get("items", []):
            if item.get("id") == item_id:
                item["feedback"] = value
                self.rewrite_digest(d, data)
                return True
        return False

    # ---- feedback ----
    def feedback_path(self, d: date) -> Path:
        return self.feedback_dir / f"{d.isoformat()}.jsonl"

    def append_feedback(self, event: dict[str, Any]) -> None:
        d = date.fromisoformat(event["ts"][:10]) if "ts" in event else datetime.now(UTC).date()
        event = {"ts": datetime.now(UTC).isoformat(), **event}
        path = self.feedback_path(d)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def read_recent_feedback(self, days: int = 30) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC).date() - timedelta(days=days)
        out: list[dict[str, Any]] = []
        if not self.feedback_dir.exists():
            return out
        for p in sorted(self.feedback_dir.glob("*.jsonl")):
            try:
                d = date.fromisoformat(p.stem)
            except ValueError:
                continue
            if d < cutoff:
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    # ---- runs ----
    def runs_path(self, d: date) -> Path:
        return self.runs_dir / f"{d.isoformat()}.jsonl"

    def append_run(self, run: dict[str, Any]) -> None:
        ts = run.get("started_at") or datetime.now(UTC).isoformat()
        d = date.fromisoformat(ts[:10])
        path = self.runs_path(d)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(run) + "\n")

    def read_recent_runs(self, days: int = 30) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC).date() - timedelta(days=days)
        out: list[dict[str, Any]] = []
        if not self.runs_dir.exists():
            return out
        for p in sorted(self.runs_dir.glob("*.jsonl")):
            try:
                d = date.fromisoformat(p.stem)
            except ValueError:
                continue
            if d < cutoff:
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        out.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        return out

    def read_run(self, run_id: str) -> dict[str, Any] | None:
        if not self.runs_dir.exists():
            return None
        for p in sorted(self.runs_dir.glob("*.jsonl"), reverse=True):
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec: dict[str, Any] = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("run_id") == run_id:
                    return rec
        return None

    # ---- reflection drain coordination ----
    @property
    def _cursor_path(self) -> Path:
        return self.state_dir / "reflection_cursor.json"

    @property
    def _lock_path(self) -> Path:
        return self.state_dir / "reflection_lock.json"

    def read_reflection_cursor(self) -> str | None:
        if not self._cursor_path.exists():
            return None
        try:
            data = json.loads(self._cursor_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        ts = data.get("last_processed_ts")
        return ts if isinstance(ts, str) else None

    def write_reflection_cursor(self, ts: str) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._cursor_path.write_text(json.dumps({"last_processed_ts": ts}), encoding="utf-8")

    def try_acquire_reflection_lock(self, *, ttl_seconds: int = 900) -> str | None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(8)
        now = datetime.now(UTC)
        body = json.dumps(
            {
                "token": token,
                "started_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            }
        )
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            # Stale-lock recovery: if expired, overwrite.
            try:
                existing = json.loads(self._lock_path.read_text(encoding="utf-8"))
                exp = datetime.fromisoformat(existing.get("expires_at", ""))
                if exp < now:
                    self._lock_path.write_text(body, encoding="utf-8")
                    return token
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        return token

    def release_reflection_lock(self, token: str) -> None:
        if not self._lock_path.exists():
            return
        try:
            existing = json.loads(self._lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if existing.get("token") == token:
            try:
                self._lock_path.unlink()
            except FileNotFoundError:
                pass

    # ---- git ----
    def git_init_if_needed(self) -> None:
        if (self.root / ".git").exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        # Use a local identity to avoid relying on global config on the Pi.
        subprocess.run(["git", "config", "user.email", "dispatch@local"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Dispatch"], cwd=self.root, check=True)

    def git_commit_all(self, message: str) -> bool:
        """Commits everything in data/. Returns True if a commit was made."""
        if not (self.root / ".git").exists():
            return False
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.root, check=False)
        if result.returncode == 0:
            return False  # nothing staged
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=self.root, check=True)
        return True


_DEFAULT_PROFILE = """# Profile

> This file is empty. Hand-edit it or use the chat tab to populate it.
> The agent treats your edits as authoritative.

## Standing interests
-

## Things I'm currently exploring
-

## Things I've explicitly said I want LESS of
-

## Voice / taste notes
-
"""


_DEFAULT_SOURCES = """# Sources for the daily digest. Edit freely; the agent reads this each run.
rss: []
youtube: []
sites: []
"""


_DEFAULT_REFLECTION_MEMORY = """# Reflection memory

_No notes yet — the next reflection agent should populate this with trends and
patterns to watch._
"""
