from __future__ import annotations

import httpx
import pytest

from digest.tools.web_fetch import _strip_html, web_fetch

SAMPLE_HTML = b"""
<html>
  <head><title>Example Article</title></head>
  <body>
    <header>nav nav nav</header>
    <article>
      <h1>Example Article</h1>
      <p>This is the first paragraph of the body, long enough to look like real content.</p>
      <p>This is the second paragraph; readability should keep it.</p>
    </article>
    <footer>copyright junk</footer>
  </body>
</html>
"""


def test_strip_html_drops_tags_keeps_paragraphs():
    out = _strip_html("<p>One.</p><p>Two.</p>")
    assert "One." in out
    assert "Two." in out
    assert "<p>" not in out


@pytest.mark.asyncio
async def test_web_fetch_extracts_main_text():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=SAMPLE_HTML)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        doc = await web_fetch("https://example.com/article", client=client)
    assert "first paragraph" in doc.text
    assert "second paragraph" in doc.text
    # readability typically drops boilerplate; we don't insist on an exact match,
    # only that body content is present.
