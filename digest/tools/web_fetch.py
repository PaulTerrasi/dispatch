"""Cheap article extraction: httpx + readability-lxml. Avoids headless Chrome."""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
from readability import Document


@dataclass(frozen=True)
class WebDocument:
    url: str
    title: str
    text: str  # plain-text-ish body (HTML tags stripped)
    html: str  # readability's cleaned HTML body


_MAX_BYTES = 2_000_000  # cap page size before handing HTML to readability

# A current browser UA. NYT (and other paywalled outlets) check the UA as
# part of bot detection — an outdated Chrome version is one of the first
# signals they flag. Bump this when Chrome ships a few major versions past
# the value here. (macOS string is frozen at 10_15_7 in real Chrome UAs.)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)


def _build_cookie_jar() -> httpx.Cookies:
    """Build an httpx cookie jar from NYT_COOKIES.

    NYT_COOKIES is a raw Cookie header value (``name=value; name=value``)
    copied from a browser session; populated from SSM at cold start. Cookies
    are scoped to ``.nytimes.com`` so httpx only sends them on requests to
    nytimes.com hosts — fetches to other domains are unaffected.
    """
    jar = httpx.Cookies()
    raw = os.environ.get("NYT_COOKIES", "").strip()
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    if not raw:
        return jar
    for pair in raw.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        jar.set(name, value, domain=".nytimes.com", path="/")
    return jar


async def web_fetch(url: str, *, client: httpx.AsyncClient | None = None) -> WebDocument:
    own_client = client is None
    c = client or httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
        cookies=_build_cookie_jar(),
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
