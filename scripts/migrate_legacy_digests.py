"""One-shot migration: legacy embedded run blocks -> separate runs/ store.

The early version of the digest writer embedded the run record inside the
digest JSON as ``digest["run"]`` and never stamped ``run_id`` onto items.
Today the runner keeps digests as ``{date, generated_at, items, agent_notes}``
with each item carrying ``run_id``, and writes runs to ``data/runs/<date>.jsonl``
via ``Store.append_run`` (see digest/runner.py:run_once).

This script walks every digest under a data directory, moves the legacy block
into the runs store with the current schema, stamps ``run_id`` on each item,
and rewrites the digest without the ``run`` / ``runs`` key. It is idempotent:
files already in the new shape are skipped.

Usage:
    uv run python scripts/migrate_legacy_digests.py [--data-dir ./data] [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from digest.store import Store


def _build_run_record(legacy: dict[str, Any], item_count: int) -> dict[str, Any]:
    """Reshape a legacy embedded run dict into a current-schema run record."""
    record: dict[str, Any] = {
        "run_id": legacy.get("run_id"),
        "kind": "curation",
        "started_at": legacy.get("started_at"),
        "duration_seconds": legacy.get("duration_seconds"),
        "tool_calls": legacy.get("tool_calls"),
        "tool_log": legacy.get("tool_log") or [],
        "profile_patches": legacy.get("profile_patches", 0),
        "sources_changed": legacy.get("sources_changed", 0),
        "reflection_notes": legacy.get("reflection_notes", ""),
        "item_count": item_count,
    }
    # Older runs sometimes used the curation_*_prompt names.
    sp = legacy.get("system_prompt", legacy.get("curation_system_prompt", ""))
    up = legacy.get("user_prompt", legacy.get("curation_user_prompt", ""))
    record["system_prompt"] = sp
    record["user_prompt"] = up
    return record


def migrate(data_dir: Path, *, dry_run: bool) -> int:
    store = Store(data_dir)
    migrated = 0
    for d in store.list_digests():
        data = store.read_digest(d)
        if not data:
            continue
        legacy = data.pop("run", None)
        # `runs` plural is also legacy when stored inside the digest file.
        legacy_list = data.pop("runs", None)
        if legacy is None and not legacy_list:
            continue

        items = data.get("items") or []
        records: list[dict[str, Any]] = []
        if legacy is not None:
            records.append(_build_run_record(legacy, item_count=len(items)))
        if legacy_list:
            for entry in legacy_list:
                if isinstance(entry, dict):
                    records.append(_build_run_record(entry, item_count=len(items)))

        first_run_id = next((r["run_id"] for r in records if r.get("run_id")), None)
        if first_run_id:
            for item in items:
                item.setdefault("run_id", first_run_id)

        print(f"{d.isoformat()}: items={len(items)} runs+={len(records)} run_id={first_run_id}")

        if dry_run:
            continue

        for record in records:
            store.append_run(record)
        store.rewrite_digest(d, data)
        migrated += 1
    return migrated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.data_dir.exists():
        raise SystemExit(f"data dir not found: {args.data_dir}")

    n = migrate(args.data_dir, dry_run=args.dry_run)
    suffix = " (dry run)" if args.dry_run else ""
    print(f"migrated {n} digest(s){suffix}")


if __name__ == "__main__":
    main()
