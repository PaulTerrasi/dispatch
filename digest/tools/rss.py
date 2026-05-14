"""RSS / Atom fetching, feedparser-backed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx


@dataclass(frozen=True)
class FeedEntry:
    title: str
    url: str
    published: datetime | None
    summary: str
    source_title: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "published": self.published.isoformat() if self.published else None,
            "summary": self.summary,
            "source_title": self.source_title,
        }


def _parse_feed(content: bytes | str, fallback_title: str = "") -> list[FeedEntry]:
    parsed = feedparser.parse(content)
    feed_title = (parsed.feed.get("title") if hasattr(parsed, "feed") else None) or fallback_title
    out: list[FeedEntry] = []
    for entry in parsed.entries:
        published: datetime | None = None
        for key in ("published_parsed", "updated_parsed"):
            tt = entry.get(key)
            if tt:
                published = datetime(tt[0], tt[1], tt[2], tt[3], tt[4], tt[5], tzinfo=UTC)
                break
        url = entry.get("link") or ""
        title = entry.get("title") or url
        summary = entry.get("summary") or ""
        out.append(
            FeedEntry(
                title=title,
                url=url,
                published=published,
                summary=summary,
                source_title=feed_title,
            )
        )
    return out


async def fetch_rss(
    url: str, *, limit: int = 25, client: httpx.AsyncClient | None = None
) -> list[FeedEntry]:
    """Fetch and parse an RSS/Atom feed at `url`."""
    own_client = client is None
    c = client or httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        headers={"User-Agent": "dispatch/0.1"},
    )
    try:
        resp = await c.get(url)
        resp.raise_for_status()
        entries = _parse_feed(resp.content, fallback_title=url)
    finally:
        if own_client:
            await c.aclose()
    return entries[:limit]
