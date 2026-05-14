"""YouTube channel uploads (via channel RSS) and transcripts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from digest.tools.rss import FeedEntry, fetch_rss


@dataclass(frozen=True)
class TranscriptResult:
    video_id: str
    text: str
    language: str | None


def channel_rss_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


async def fetch_youtube_channel(
    channel_id: str, *, limit: int = 15, client: httpx.AsyncClient | None = None
) -> list[FeedEntry]:
    return await fetch_rss(channel_rss_url(channel_id), limit=limit, client=client)


async def fetch_youtube_transcript(video_id: str) -> TranscriptResult:
    """Fetch a transcript for a YouTube video. youtube-transcript-api is sync,
    so we offload to a thread."""
    from youtube_transcript_api import YouTubeTranscriptApi

    def _run() -> TranscriptResult:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id)
        snippets = list(fetched)
        text = "\n".join(s.text for s in snippets)
        language = snippets[0].language if snippets and hasattr(snippets[0], "language") else None
        return TranscriptResult(video_id=video_id, text=text, language=language)

    return await asyncio.to_thread(_run)
