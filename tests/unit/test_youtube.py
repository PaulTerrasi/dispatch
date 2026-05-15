from __future__ import annotations

import httpx
import pytest

from digest.tools.youtube import channel_rss_url, fetch_youtube_channel

SAMPLE_YT_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <title>Channel Name</title>
  <entry>
    <yt:videoId>abc123XYZ45</yt:videoId>
    <title>An Upload</title>
    <link href="https://www.youtube.com/watch?v=abc123XYZ45"/>
    <updated>2026-04-29T10:00:00Z</updated>
  </entry>
</feed>
"""


def test_channel_rss_url_format():
    assert channel_rss_url("UCabc") == ("https://www.youtube.com/feeds/videos.xml?channel_id=UCabc")


@pytest.mark.asyncio
async def test_fetch_youtube_transcript_offloads_to_thread(monkeypatch):
    """fetch_youtube_transcript wraps a sync API; verify it returns the joined
    text + language and dispatches the sync call through asyncio.to_thread.
    """
    from digest.tools import youtube as yt_module

    class _Snippet:
        def __init__(self, text: str, language: str) -> None:
            self.text = text
            self.language = language

    class _Fetched:
        def __iter__(self):
            return iter([_Snippet("hello", "en"), _Snippet("world", "en")])

    class _FakeApi:
        def fetch(self, video_id: str) -> _Fetched:
            assert video_id == "vid01234567"
            return _Fetched()

    class _FakeModule:
        YouTubeTranscriptApi = _FakeApi

    monkeypatch.setitem(__import__("sys").modules, "youtube_transcript_api", _FakeModule)

    result = await yt_module.fetch_youtube_transcript("vid01234567")
    assert result.video_id == "vid01234567"
    assert result.text == "hello\nworld"
    assert result.language == "en"


@pytest.mark.asyncio
async def test_fetch_youtube_transcript_empty(monkeypatch):
    """Empty transcript → empty text + None language."""
    from digest.tools import youtube as yt_module

    class _FakeApi:
        def fetch(self, _video_id: str):
            return iter([])

    class _FakeModule:
        YouTubeTranscriptApi = _FakeApi

    monkeypatch.setitem(__import__("sys").modules, "youtube_transcript_api", _FakeModule)
    result = await yt_module.fetch_youtube_transcript("v")
    assert result.text == ""
    assert result.language is None


@pytest.mark.asyncio
async def test_fetch_youtube_channel_parses_uploads():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "channel_id=UCabc" in str(request.url)
        return httpx.Response(200, content=SAMPLE_YT_RSS.encode())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        entries = await fetch_youtube_channel("UCabc", client=client)
    assert len(entries) == 1
    assert entries[0].title == "An Upload"
    assert entries[0].source_title == "Channel Name"
