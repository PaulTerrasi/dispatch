"""S3-backed store: same public interface as digest.store.Store but backed by Amazon S3.

Object layout in S3 mirrors the data/ filesystem layout:
  profile.md          → s3://{bucket}/profile.md
  sources.yaml        → s3://{bucket}/sources.yaml
  digests/{date}.json → s3://{bucket}/digests/{date}.json
  feedback/{date}.jsonl → s3://{bucket}/feedback/{date}.jsonl
  runs/{date}.jsonl   → s3://{bucket}/runs/{date}.jsonl
  state/reflection_cursor.json, state/reflection_lock.json

S3 versioning on the bucket serves as the audit trail (replaces git).
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import yaml

from digest import clock
from digest.store import Source

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


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


class S3Store:
    def __init__(self, bucket: str) -> None:
        import boto3

        self.bucket = bucket
        self.s3: S3Client = boto3.client("s3")
        self._digest_cache: dict[date, dict[str, Any]] = {}
        self._list_cache: list[date] | None = None
        self._list_cache_ts: float = 0.0

    def _get(self, key: str) -> str | None:
        """Return object body as text, or None if the key doesn't exist."""
        from botocore.exceptions import ClientError

        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read().decode("utf-8")
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    def _put(self, key: str, body: str, content_type: str = "text/plain") -> None:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType=content_type,
        )

    def ensure_layout(self) -> None:
        # S3 has no directories; seed defaults if objects don't exist yet.
        if self._get("profile.md") is None:
            self._put("profile.md", _DEFAULT_PROFILE, "text/markdown")
        if self._get("sources.yaml") is None:
            self._put("sources.yaml", _DEFAULT_SOURCES, "application/yaml")
        if self._get("reflection_memory.md") is None:
            self._put("reflection_memory.md", _DEFAULT_REFLECTION_MEMORY, "text/markdown")

    # ---- profile ----

    def read_profile(self) -> str:
        return self._get("profile.md") or _DEFAULT_PROFILE

    def write_profile(self, text: str) -> None:
        self._put("profile.md", text, "text/markdown")

    # ---- reflection memory ----
    # Single-writer by construction (serialized by the reflection lock).
    def read_reflection_memory(self) -> str:
        return self._get("reflection_memory.md") or _DEFAULT_REFLECTION_MEMORY

    def write_reflection_memory(self, text: str) -> None:
        self._put("reflection_memory.md", text, "text/markdown")

    # ---- sources ----

    def list_sources(self) -> list[Source]:
        raw_text = self._get("sources.yaml")
        if raw_text is None:
            return []
        raw = yaml.safe_load(raw_text) or {}
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

    def write_sources(self, sources: list[Source]) -> None:
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
        self._put(
            "sources.yaml",
            header + yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            "application/yaml",
        )

    # ---- digests ----

    def write_digest(self, d: date, items: list[dict[str, Any]], agent_notes: str) -> None:
        existing_text = self._get(f"digests/{d.isoformat()}.json")
        if existing_text is not None:
            existing: dict[str, Any] = json.loads(existing_text)
            existing_ids = {i.get("id") for i in existing.get("items", []) or []}
            for item in items:
                if item.get("id") not in existing_ids:
                    existing.setdefault("items", []).append(item)
                    existing_ids.add(item.get("id"))
            existing["agent_notes"] = agent_notes
            payload = existing
        else:
            payload = {
                "date": d.isoformat(),
                "generated_at": datetime.now(UTC).isoformat(),
                "items": items,
                "agent_notes": agent_notes,
            }
        self._digest_cache.pop(d, None)
        self._list_cache = None
        self._put(
            f"digests/{d.isoformat()}.json",
            json.dumps(payload, indent=2),
            "application/json",
        )

    def rewrite_digest(self, d: date, data: dict[str, Any]) -> None:
        self._digest_cache.pop(d, None)
        self._put(
            f"digests/{d.isoformat()}.json",
            json.dumps(data, indent=2),
            "application/json",
        )

    def read_digest(self, d: date) -> dict[str, Any] | None:
        if d in self._digest_cache:
            return self._digest_cache[d]
        text = self._get(f"digests/{d.isoformat()}.json")
        if text is None:
            return None
        result: dict[str, Any] = json.loads(text)
        self._digest_cache[d] = result
        return result

    def list_digests(self) -> list[date]:
        import time

        if self._list_cache is not None and time.time() - self._list_cache_ts < 60:
            return self._list_cache
        paginator = self.s3.get_paginator("list_objects_v2")
        out: list[date] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix="digests/"):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                stem = key.removeprefix("digests/").removesuffix(".json")
                try:
                    out.append(date.fromisoformat(stem))
                except ValueError:
                    continue
        result = sorted(out, reverse=True)
        self._list_cache = result
        self._list_cache_ts = time.time()
        return result

    def recent_digest_items(self, days: int = 7) -> list[dict[str, str]]:
        cutoff = clock.today() - timedelta(days=days)
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

    def append_feedback(self, event: dict[str, Any]) -> None:
        from botocore.exceptions import ClientError

        # Stamp with the current UTC instant (a caller-supplied ts wins), then
        # bucket the object by the app-timezone calendar day of that instant.
        event = {"ts": datetime.now(UTC).isoformat(), **event}
        d = clock.day_of(event["ts"])
        key = f"feedback/{d.isoformat()}.jsonl"
        try:
            existing = (
                self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8")
            )
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                existing = ""
            else:
                raise
        new_content = existing + json.dumps(event) + "\n"
        self._put(key, new_content, "application/x-ndjson")

    def read_recent_feedback(self, days: int = 30) -> list[dict[str, Any]]:
        cutoff = clock.today() - timedelta(days=days)
        paginator = self.s3.get_paginator("list_objects_v2")
        keys: list[tuple[date, str]] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix="feedback/"):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                stem = key.removeprefix("feedback/").removesuffix(".jsonl")
                try:
                    d = date.fromisoformat(stem)
                except ValueError:
                    continue
                if d >= cutoff:
                    keys.append((d, key))

        out: list[dict[str, Any]] = []
        for _, key in sorted(keys):
            text = self._get(key)
            if not text:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    # ---- runs ----

    def append_run(self, run: dict[str, Any]) -> None:
        from botocore.exceptions import ClientError

        ts = run.get("started_at") or datetime.now(UTC).isoformat()
        d = clock.day_of(ts)
        key = f"runs/{d.isoformat()}.jsonl"
        try:
            existing = (
                self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8")
            )
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                existing = ""
            else:
                raise
        new_content = existing + json.dumps(run) + "\n"
        self._put(key, new_content, "application/x-ndjson")

    def read_recent_runs(self, days: int = 30) -> list[dict[str, Any]]:
        cutoff = clock.today() - timedelta(days=days)
        paginator = self.s3.get_paginator("list_objects_v2")
        keys: list[tuple[date, str]] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix="runs/"):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                stem = key.removeprefix("runs/").removesuffix(".jsonl")
                try:
                    d = date.fromisoformat(stem)
                except ValueError:
                    continue
                if d >= cutoff:
                    keys.append((d, key))
        out: list[dict[str, Any]] = []
        for _, key in sorted(keys):
            text = self._get(key)
            if not text:
                continue
            for line in text.splitlines():
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
        paginator = self.s3.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix="runs/"):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        for key in sorted(keys, reverse=True):
            text = self._get(key)
            if not text:
                continue
            for line in text.splitlines():
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

    _CURSOR_KEY = "state/reflection_cursor.json"
    _LOCK_KEY = "state/reflection_lock.json"

    def read_reflection_cursor(self) -> str | None:
        text = self._get(self._CURSOR_KEY)
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        ts = data.get("last_processed_ts")
        return ts if isinstance(ts, str) else None

    def write_reflection_cursor(self, ts: str) -> None:
        self._put(
            self._CURSOR_KEY,
            json.dumps({"last_processed_ts": ts}),
            "application/json",
        )

    def try_acquire_reflection_lock(self, *, ttl_seconds: int = 900) -> str | None:
        from botocore.exceptions import ClientError

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
            self.s3.put_object(
                Bucket=self.bucket,
                Key=self._LOCK_KEY,
                Body=body.encode("utf-8"),
                ContentType="application/json",
                IfNoneMatch="*",
            )
            return token
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in ("PreconditionFailed", "412"):
                raise
        # Lock exists — check if it's stale and try to steal.
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=self._LOCK_KEY)
        except ClientError:
            return None
        etag = resp.get("ETag")
        try:
            existing = json.loads(resp["Body"].read().decode("utf-8"))
            exp = datetime.fromisoformat(existing.get("expires_at", ""))
        except (json.JSONDecodeError, ValueError, KeyError):
            return None
        if exp >= now:
            return None
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=self._LOCK_KEY,
                Body=body.encode("utf-8"),
                ContentType="application/json",
                IfMatch=etag,
            )
            return token
        except ClientError:
            return None

    def release_reflection_lock(self, token: str) -> None:
        from botocore.exceptions import ClientError

        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=self._LOCK_KEY)
        except ClientError:
            return
        try:
            existing = json.loads(resp["Body"].read().decode("utf-8"))
        except json.JSONDecodeError:
            return
        if existing.get("token") != token:
            return
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=self._LOCK_KEY)
        except ClientError:
            pass

    # ---- git (no-ops: S3 versioning replaces git audit trail) ----

    def git_init_if_needed(self) -> None:
        pass

    def git_commit_all(self, message: str) -> bool:
        return False
