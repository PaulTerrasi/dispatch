"""Seed ./data with a believable 7-day demo dataset for `make dev`.

Writes through digest.store.Store only — no direct filesystem IO outside
the store API, matching the project's storage discipline.
"""

from __future__ import annotations

import argparse
import secrets
import shutil
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from digest.store import Source, Store

PROFILE_MD = """# Profile

> Populated by scripts/seed_demo_data.py for local demos. Edit freely —
> the agent treats your edits as authoritative.

## Standing interests
- AI / LLM infrastructure: inference serving, agent frameworks, evals
- Software engineering craft: type systems, refactoring, codebase health
- Distributed systems & databases: consensus, storage internals, query planning
- Developer tooling: editors, shells, debuggers, observability
- The economics of platforms: pricing, distribution, lock-in dynamics

## Things I'm currently exploring
- Local-first software patterns and CRDTs
- Building agentic workflows with Claude
- Postgres under high write throughput
- Rust for systems work

## Things I've explicitly said I want LESS of
- Crypto / web3 speculation pieces
- Generic startup advice / VC takes
- "X is dead, long live Y" framing posts

## Voice / taste notes
- Prefer technical depth over executive summaries
- Tolerate long-form (30–60 min reads) if the payoff is concrete
- Skeptical of vendor blog posts unless they include numbers
- Engineering blogs with code > essays without
"""

REFLECTION_MEMORY_MD = """# Reflection memory

- The user reliably thumbs-up posts that include concrete benchmarks or
  production incident write-ups; surface those over opinion pieces.
- AI agent posts get mixed reactions — prefer ones with code or evals,
  skip pure speculation about AGI timelines.
- Postgres internals content lands well; broader "SQL is back" takes do not.
- Local-first / CRDT content was thumbed-up three times this month — keep
  feeding this thread.
- Avoid framing-bait headlines ("X is dead", "the end of Y"). Two recent
  thumbs-down match this pattern.
- Long-form (30+ min) is fine when the source has earned trust
  (Stratechery, Pragmatic Engineer); be stingier with unknown sources.
- The user has not engaged with any crypto content in 60+ days — deprioritize.
"""


SOURCES = [
    Source(kind="rss", value="https://stratechery.com/feed/", tags=["strategy", "platforms"]),
    Source(kind="rss", value="https://news.ycombinator.com/rss", tags=["tech", "discussion"]),
    Source(kind="rss", value="https://simonwillison.net/atom/everything/", tags=["ai", "python"]),
    Source(kind="rss", value="https://jvns.ca/atom.xml", tags=["systems", "debugging"]),
    Source(kind="rss", value="https://newsletter.pragmaticengineer.com/feed", tags=["engineering"]),
    Source(kind="rss", value="https://export.arxiv.org/rss/cs.AI", tags=["ai", "research"]),
    Source(
        kind="youtube",
        value="UCsBjURrPoezykLs9EqgamOA",
        name="Fireship",
        tags=["tech", "shorts"],
    ),
    Source(
        kind="youtube",
        value="UCbfYPyITQ-7l4upoX8nvctg",
        name="Two Minute Papers",
        tags=["ai", "research"],
    ),
    Source(kind="site", value="https://www.brendangregg.com/blog/"),
    Source(kind="site", value="https://blog.danslimmon.com/"),
]


# Pool of realistic items. Each entry becomes a DigestItem dict.
ITEM_POOL: list[dict[str, object]] = [
    {
        "type": "article",
        "title": "How Postgres handles 100k writes per second at Figma",
        "source": "Figma Engineering",
        "url": "https://www.figma.com/blog/how-figma-scales-postgres/",
        "summary": (
            "Figma describes their sharding approach for Postgres under sustained "
            "high write throughput. Notable: they avoided logical replication and "
            "built routing at the application layer instead."
        ),
    },
    {
        "type": "article",
        "title": "Local-first software: you own your data, in spite of the cloud",
        "source": "Ink & Switch",
        "url": "https://www.inkandswitch.com/local-first/",
        "summary": (
            "A foundational essay on local-first principles. Walks through seven "
            "ideals and evaluates existing tech (CRDTs, sync engines) against them."
        ),
    },
    {
        "type": "article",
        "title": "Why we ripped out Kafka and went back to Postgres",
        "source": "Pragmatic Engineer",
        "url": "https://newsletter.pragmaticengineer.com/p/kafka-to-postgres",
        "summary": (
            "A team's post-mortem on choosing Kafka too early. Latency was fine; "
            "operational overhead was not. Migration plan and the numbers behind it."
        ),
    },
    {
        "type": "article",
        "title": "Notes on running an LLM eval suite that actually catches regressions",
        "source": "Simon Willison",
        "url": "https://simonwillison.net/2026/Apr/llm-evals/",
        "summary": (
            "Concrete patterns for writing LLM evals that don't just measure "
            "vibes. Includes a small library and three real failure modes caught "
            "in production."
        ),
    },
    {
        "type": "article",
        "title": "The hidden cost of every microservice boundary",
        "source": "Dan Slimmon",
        "url": "https://blog.danslimmon.com/2026/03/microservice-cost/",
        "summary": (
            "An incident-response-focused argument for fewer service boundaries. "
            "Each one is a serialization format and a debugging seam you'll pay for."
        ),
    },
    {
        "type": "article",
        "title": "Tracing a mysterious 200ms latency spike to TCP_NODELAY",
        "source": "Julia Evans",
        "url": "https://jvns.ca/blog/2026/04/tcp-nodelay/",
        "summary": (
            "A debugging story that ends with Nagle's algorithm and a one-line "
            "fix. Useful refresher on what TCP_NODELAY actually does."
        ),
    },
    {
        "type": "article",
        "title": "Aggregation theory revisited in the age of AI distribution",
        "source": "Stratechery",
        "url": "https://stratechery.com/2026/aggregation-ai/",
        "summary": (
            "Ben Thompson re-examines aggregation theory as AI assistants become "
            "the new front door. The interesting twist: the aggregator might be "
            "the model, not the app."
        ),
    },
    {
        "type": "article",
        "title": "Building a CRDT-backed collaborative editor in 500 lines",
        "source": "Ink & Switch",
        "url": "https://www.inkandswitch.com/crdt-editor-500/",
        "summary": (
            "A minimal but real CRDT text editor. Source is readable in one "
            "sitting; performance characteristics are discussed at the end."
        ),
    },
    {
        "type": "article",
        "title": "Anthropic publishes Claude 4.7 system card with new agent evals",
        "source": "Anthropic",
        "url": "https://www.anthropic.com/news/claude-4-7-system-card",
        "summary": (
            "Includes agentic-task benchmarks (SWE-bench, OSWorld) and a section "
            "on long-horizon eval methodology. Worth a read for anyone building "
            "agents."
        ),
    },
    {
        "type": "article",
        "title": "Rust's async story is finally good, with caveats",
        "source": "Without Boats",
        "url": "https://without.boats/blog/2026/async-good/",
        "summary": (
            "A measured update from a former async-Rust working group member. "
            "Things that work now, things that still bite, and where the next "
            "improvements are landing."
        ),
    },
    {
        "type": "article",
        "title": "Show HN: a tiny in-process queue that survives crashes",
        "source": "Hacker News",
        "url": "https://news.ycombinator.com/item?id=39000001",
        "summary": (
            "A 400-LOC durable queue using mmap + a write-ahead log. Comments "
            "include benchmarks against SQLite and LMDB."
        ),
    },
    {
        "type": "article",
        "title": "What I learned reviewing 200 LLM agent codebases",
        "source": "Pragmatic Engineer",
        "url": "https://newsletter.pragmaticengineer.com/p/agent-patterns",
        "summary": (
            "Patterns and anti-patterns across real agent codebases. The "
            "consistent finding: tool design matters more than model choice."
        ),
    },
    {
        "type": "article",
        "title": "Why your indexes are slower than you think",
        "source": "Crunchy Data",
        "url": "https://www.crunchydata.com/blog/postgres-index-cost/",
        "summary": (
            "Cost of index maintenance under high update load, with real "
            "numbers. Three guidelines for deciding what to index."
        ),
    },
    {
        "type": "article",
        "title": "BPF for application developers: a gentle introduction",
        "source": "Brendan Gregg",
        "url": "https://www.brendangregg.com/blog/2026-03-15/bpf-app-devs.html",
        "summary": (
            "Aimed at app devs who've heard 'use eBPF' but never have. Five "
            "things you can do today with bpftrace one-liners."
        ),
    },
    {
        "type": "article",
        "title": "How Linear builds: tiny teams, ruthless scope",
        "source": "Linear Blog",
        "url": "https://linear.app/blog/how-linear-builds",
        "summary": (
            "Linear's product team writes about scoping projects to two-week "
            "ships and what they cut to make it work."
        ),
    },
    {
        "type": "video",
        "title": "Bun 2.0 in 100 seconds",
        "source": "Fireship",
        "url": "https://www.youtube.com/watch?v=demo-fireship-bun2",
        "duration_min": 2,
        "summary": (
            "Quick rundown of what's new in Bun 2.0: improved Node compat, "
            "faster install, and a new test runner."
        ),
    },
    {
        "type": "video",
        "title": "DeepMind's new agent achieves 78% on SWE-bench",
        "source": "Two Minute Papers",
        "url": "https://www.youtube.com/watch?v=demo-tmp-swe",
        "duration_min": 7,
        "summary": (
            "Walkthrough of DeepMind's latest paper. Notable: most gains come "
            "from better tool design and not from a larger model."
        ),
    },
    {
        "type": "video",
        "title": "Live-coding a tiny database engine in Zig",
        "source": "TigerBeetle",
        "url": "https://www.youtube.com/watch?v=demo-tb-zig-db",
        "duration_min": 42,
        "summary": (
            "From scratch: storage layout, WAL, and a query path. Pace is "
            "fast but the code is readable and committed to GitHub."
        ),
    },
    {
        "type": "video",
        "title": "Postgres internals: how MVCC really works",
        "source": "CMU Database Group",
        "url": "https://www.youtube.com/watch?v=demo-cmu-mvcc",
        "duration_min": 58,
        "summary": (
            "Lecture from the CMU advanced DB course. The xmin/xmax mechanics "
            "section at minute 22 is the clearest explanation I've seen."
        ),
    },
    {
        "type": "article",
        "title": "Designing tools for AI agents, not for humans",
        "source": "Anthropic Engineering",
        "url": "https://www.anthropic.com/engineering/agent-tool-design",
        "summary": (
            "Tool ergonomics for agents differ from CLI ergonomics. Concrete "
            "examples: error messages should be self-correcting, not terse."
        ),
    },
    {
        "type": "article",
        "title": "The end of the standalone code editor",
        "source": "Some Substack",
        "url": "https://example.substack.com/p/end-of-editor",
        "summary": (
            "Argues that AI-native editors will eat the standalone editor market "
            "within two years. Light on evidence, heavy on framing."
        ),
    },
    {
        "type": "article",
        "title": "Why crypto is finally about to break through (again)",
        "source": "Some VC Blog",
        "url": "https://example-vc.com/crypto-2026",
        "summary": (
            "Another round of 'this time it's different' takes on crypto. "
            "No new data, mostly vibes."
        ),
    },
]


def _build_curation_run(
    run_id: str,
    started_at: datetime,
    item_count: int,
) -> dict[str, object]:
    duration = 180.0 + (hash(run_id) % 240)
    tool_log: list[dict[str, object]] = [
        {
            "ts": (started_at + timedelta(seconds=2)).isoformat(),
            "tool": "read_profile",
            "args": {},
            "outcome": "loaded 4 sections, 17 bullets",
        },
        {
            "ts": (started_at + timedelta(seconds=5)).isoformat(),
            "tool": "read_sources",
            "args": {},
            "outcome": "10 sources (6 rss, 2 youtube, 2 sites)",
        },
        {
            "ts": (started_at + timedelta(seconds=12)).isoformat(),
            "tool": "fetch_rss",
            "args": {"url": "https://news.ycombinator.com/rss"},
            "outcome": "fetched 30 entries",
            "thinking": (
                "HN front page is noisy; I'll filter to items with >100 points "
                "and matching the user's standing interests."
            ),
        },
        {
            "ts": (started_at + timedelta(seconds=34)).isoformat(),
            "tool": "fetch_rss",
            "args": {"url": "https://simonwillison.net/atom/everything/"},
            "outcome": "fetched 8 entries, 2 candidates",
        },
        {
            "ts": (started_at + timedelta(seconds=58)).isoformat(),
            "tool": "dedup_against_recent",
            "args": {"window_days": 7},
            "outcome": "removed 3 duplicates",
        },
        {
            "ts": (started_at + timedelta(seconds=int(duration) - 5)).isoformat(),
            "tool": "write_digest",
            "args": {"item_count": item_count},
            "outcome": "ok",
        },
    ]
    return {
        "run_id": run_id,
        "kind": "curation",
        "started_at": started_at.isoformat(),
        "duration_seconds": duration,
        "tool_calls": len(tool_log) + 8,
        "tool_log": tool_log,
        "profile_patches": 0,
        "sources_changed": 0,
        "reflection_notes": "",
        "system_prompt": (
            "You are the curation agent for the user's morning digest. Read "
            "profile.md and sources.yaml, fetch new items, dedup against the "
            "last 7 days, and write 4–8 items into today's digest."
        ),
        "user_prompt": "Curate today's digest.",
        "item_count": item_count,
        "exit_reason": "completed",
    }


def _build_reflection_run(
    run_id: str,
    started_at: datetime,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "kind": "reflection",
        "started_at": started_at.isoformat(),
        "duration_seconds": 95.0 + (hash(run_id) % 60),
        "tool_calls": 6,
        "tool_log": [
            {
                "ts": (started_at + timedelta(seconds=3)).isoformat(),
                "tool": "read_recent_feedback",
                "args": {"days": 14},
                "outcome": "loaded 11 feedback events",
            },
            {
                "ts": (started_at + timedelta(seconds=20)).isoformat(),
                "tool": "read_reflection_memory",
                "args": {},
                "outcome": "loaded 7 bullets",
            },
            {
                "ts": (started_at + timedelta(seconds=80)).isoformat(),
                "tool": "write_reflection_memory",
                "args": {},
                "outcome": "updated 2 bullets",
            },
        ],
        "profile_patches": 0,
        "sources_changed": 0,
        "reflection_notes": (
            "Thumbs-up rate is highest on posts with code or benchmarks. "
            "Crypto content continues to get zero engagement — keeping it "
            "deprioritized."
        ),
        "system_prompt": (
            "You are the reflection agent. Read recent feedback and update "
            "reflection_memory.md with patterns the curation agent should know."
        ),
        "user_prompt": "Reflect on the last two weeks of feedback.",
        "item_count": 0,
        "exit_reason": "completed",
    }


AGENT_NOTES_POOL = [
    "Quieter day on the feeds — front-loaded with two HN deep-dives and a Postgres internals piece. Skipped a third crypto take.",
    "Heavy AI-infra day. Two Anthropic posts; included both since one is the system card and the other is engineering practice.",
    "Mixed bag — one strategy piece, two debugging stories, and a CRDT video. Dropped a 'X is dead' framing post per your taste notes.",
    "Lighter feed today, padded with one CMU lecture you'd flagged interest in last week.",
    "Several local-first / CRDT items hit at once; surfaced the strongest two and held the rest for tomorrow.",
    "Pragmatic Engineer dropped two longform pieces. Included the one with concrete numbers; skipped the more speculative one.",
    "Slow news day. Pulled a couple of older but evergreen Postgres pieces from sites you've thumbed-up before.",
]


def seed(data_dir: Path, days: int, force: bool) -> None:
    if force:
        for sub in ("digests", "runs", "feedback", "state"):
            target = data_dir / sub
            if target.exists():
                shutil.rmtree(target)

    store = Store(data_dir)
    store.ensure_layout()
    store.write_profile(PROFILE_MD)
    store.write_sources(SOURCES)
    store.write_reflection_memory(REFLECTION_MEMORY_MD)

    today = datetime.now(UTC).date()
    pool_len = len(ITEM_POOL)

    for offset in range(days):
        d = today - timedelta(days=offset)
        # 4–7 items per day, deterministic per-day but varied.
        seed_n = (offset * 7) % pool_len
        item_count = 4 + (offset % 4)

        run_started = datetime.combine(d, time(hour=5, minute=30), tzinfo=UTC)
        curation_run_id = f"cur{d.strftime('%Y%m%d')}{secrets.token_hex(2)}"

        items: list[dict[str, object]] = []
        for i in range(item_count):
            template = ITEM_POOL[(seed_n + i) % pool_len]
            item = {
                "id": secrets.token_hex(4),
                "type": template["type"],
                "title": template["title"],
                "source": template["source"],
                "url": template["url"],
                "summary": template["summary"],
                "duration_min": template.get("duration_min"),
                "feedback": None,
                "run_id": curation_run_id,
            }
            # Older items: ~40% have feedback. Today (offset 0) and yesterday: none.
            if offset >= 2 and (i + offset) % 3 == 0:
                item["feedback"] = "up" if (i + offset) % 2 == 0 else "down"
            items.append(item)

        notes = AGENT_NOTES_POOL[offset % len(AGENT_NOTES_POOL)]
        store.write_digest(d, items, notes)

        store.append_run(_build_curation_run(curation_run_id, run_started, item_count))

        # Reflection run every other day, in the afternoon.
        if offset % 2 == 1:
            refl_id = f"ref{d.strftime('%Y%m%d')}{secrets.token_hex(2)}"
            refl_started = datetime.combine(d, time(hour=17, minute=0), tzinfo=UTC)
            store.append_run(_build_reflection_run(refl_id, refl_started))

        # Feedback events for items that got reactions.
        for item in items:
            if item["feedback"] is not None:
                fb_ts = datetime.combine(d, time(hour=8, minute=15), tzinfo=UTC)
                store.append_feedback(
                    {
                        "ts": fb_ts.isoformat(),
                        "kind": "thumb",
                        "value": item["feedback"],
                        "item_id": item["id"],
                        "digest_date": d.isoformat(),
                    }
                )

    print(f"Seeded {days} days of demo data into {data_dir}/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="./data", type=Path)
    parser.add_argument("--days", default=7, type=int)
    parser.add_argument("--force", action="store_true", help="wipe existing digests/runs/feedback first")
    args = parser.parse_args()
    seed(args.data_dir, args.days, args.force)


if __name__ == "__main__":
    main()
