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
