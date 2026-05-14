"""Cheap article extraction: httpx + readability-lxml. Avoids headless Chrome."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from readability import Document


@dataclass(frozen=True)
class WebDocument:
    url: str
    title: str
    text: str  # plain-text-ish body (HTML tags stripped)
    html: str  # readability's cleaned HTML body


_MAX_BYTES = 2_000_000  # don't try to read 50MB pages on a Pi


async def web_fetch(url: str, *, client: httpx.AsyncClient | None = None) -> WebDocument:
    own_client = client is None
    c = client or httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "dispatch/0.1"},
    )
    try:
        resp = await c.get(url)
        resp.raise_for_status()
        # readability expects text; decode using the response's detected encoding.
        text = resp.text
        if len(text) > _MAX_BYTES:
            text = text[:_MAX_BYTES]
    finally:
        if own_client:
            await c.aclose()

    doc = Document(text)
    cleaned_html = doc.summary(html_partial=True)
    title = doc.short_title() or url
    text = _strip_html(cleaned_html)
    return WebDocument(url=url, title=title, text=text, html=cleaned_html)


def _strip_html(html: str) -> str:
    """Light-touch HTML-to-text. Avoids importing BeautifulSoup just for this."""
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self.parts.append(data)

        def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
            if tag in {"p", "br", "li", "div", "h1", "h2", "h3", "h4"}:
                self.parts.append("\n")

    s = _Stripper()
    s.feed(html)
    text = "".join(s.parts)
    # Collapse runs of whitespace but preserve paragraph breaks.
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)
