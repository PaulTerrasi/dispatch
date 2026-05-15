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


def test_parse_feed_entry_with_no_date_keys():
    """An entry with neither `published` nor `updated` should leave `published=None`."""
    no_date = (
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Dateless</title>"
        '<entry><title>No date</title><link href="https://x/1"/></entry>'
        "</feed>"
    )
    entries = _parse_feed(no_date)
    assert entries[0].published is None


def test_feed_entry_as_dict_handles_no_publish_date():
    from digest.tools.rss import FeedEntry

    e = FeedEntry(title="t", url="u", published=None, summary="s", source_title="src")
    assert e.as_dict()["published"] is None


def test_feed_entry_as_dict_serializes_publish_date():
    from digest.tools.rss import FeedEntry

    e = FeedEntry(
        title="t",
        url="u",
        published=datetime(2026, 4, 29, 10, 0, tzinfo=UTC),
        summary="s",
        source_title="src",
    )
    assert e.as_dict()["published"] == "2026-04-29T10:00:00+00:00"


@pytest.mark.asyncio
async def test_fetch_rss_truncates_to_limit():
    """When the feed has N entries but limit < N, only `limit` entries are returned."""

    big_feed = (
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Many</title>"
        + "".join(
            f"<entry><title>e{i}</title><link href='https://x/{i}'/>"
            f"<updated>2026-04-{(i % 27) + 1:02d}T10:00:00Z</updated></entry>"
            for i in range(10)
        )
        + "</feed>"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big_feed.encode())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        entries = await fetch_rss("https://example.com/feed", client=client, limit=3)
    assert len(entries) == 3


@pytest.mark.asyncio
async def test_fetch_rss_creates_and_closes_default_client(monkeypatch):
    """fetch_rss(..., client=None) builds its own client and closes it on exit.

    We patch httpx.AsyncClient to route through MockTransport while recording
    close() so we can verify the lifecycle without hitting the network.
    """
    import digest.tools.rss as rss_module

    closed: dict[str, bool] = {"value": False}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=SAMPLE_ATOM.encode())

    real_client_cls = rss_module.httpx.AsyncClient

    class _TrackingClient(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

        async def aclose(self) -> None:
            closed["value"] = True
            await super().aclose()

    monkeypatch.setattr(rss_module.httpx, "AsyncClient", _TrackingClient)
    entries = await fetch_rss("https://example.com/feed")
    assert len(entries) == 2
    assert closed["value"] is True


@pytest.mark.asyncio
async def test_fetch_rss_raises_on_error_status():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_rss("https://example.com/feed", client=client)
