from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from digest.tools.rss import _parse_feed, fetch_rss

SAMPLE_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Feed</title>
  <entry>
    <title>First Post</title>
    <link href="https://example.com/1"/>
    <updated>2026-04-29T10:00:00Z</updated>
    <summary>An entry.</summary>
  </entry>
  <entry>
    <title>Second Post</title>
    <link href="https://example.com/2"/>
    <updated>2026-04-28T09:00:00Z</updated>
    <summary>Another entry.</summary>
  </entry>
</feed>
"""


def test_parse_feed_extracts_entries():
    entries = _parse_feed(SAMPLE_ATOM)
    assert [e.title for e in entries] == ["First Post", "Second Post"]
    assert all(e.source_title == "Example Feed" for e in entries)
    assert entries[0].published == datetime(2026, 4, 29, 10, 0, tzinfo=UTC)
    assert entries[0].url == "https://example.com/1"


@pytest.mark.asyncio
async def test_fetch_rss_uses_httpx_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/feed"
        return httpx.Response(200, content=SAMPLE_ATOM.encode())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        entries = await fetch_rss("https://example.com/feed", client=client, limit=5)
    assert len(entries) == 2
    assert entries[0].title == "First Post"


@pytest.mark.asyncio
async def test_fetch_rss_raises_on_error_status():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_rss("https://example.com/feed", client=client)
