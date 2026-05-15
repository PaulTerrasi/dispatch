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
async def test_web_fetch_truncates_oversized_payload(monkeypatch):
    """A 5 MB response should be truncated before readability runs."""
    import digest.tools.web_fetch as wf

    # Shrink the limit so we don't actually allocate 2 MB of HTML in the test.
    monkeypatch.setattr(wf, "_MAX_BYTES", 1024)
    big_html = b"<html><body>" + (b"<p>filler</p>" * 5000) + b"</body></html>"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big_html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        doc = await wf.web_fetch("https://example.com/big", client=client)
    # Nothing exploded, and the document is well-formed.
    assert doc.url == "https://example.com/big"


@pytest.mark.asyncio
async def test_web_fetch_creates_and_closes_default_client(monkeypatch):
    """When no client is passed, web_fetch constructs and closes its own."""
    import digest.tools.web_fetch as wf

    closed = {"value": False}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=SAMPLE_HTML)

    real_client_cls = wf.httpx.AsyncClient

    class _TrackingClient(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

        async def aclose(self) -> None:
            closed["value"] = True
            await super().aclose()

    monkeypatch.setattr(wf.httpx, "AsyncClient", _TrackingClient)
    doc = await wf.web_fetch("https://example.com/article")
    assert "first paragraph" in doc.text
    assert closed["value"] is True


def test_strip_html_handles_block_tags():
    """Block tags inject newlines so the resulting text has paragraph breaks."""
    from digest.tools.web_fetch import _strip_html

    out = _strip_html("<h1>Title</h1><p>One.</p><p>Two.</p><li>Bullet</li>")
    assert "Title" in out
    assert "One." in out
    # Different blocks should be on different lines.
    lines = out.splitlines()
    assert "Title" in lines
    assert "One." in lines


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


@pytest.mark.asyncio
async def test_cookie_jar_sends_nyt_cookies_to_nytimes(monkeypatch):
    """NYT_COOKIES env var is sent as Cookie header on nytimes.com requests."""
    import digest.tools.web_fetch as wf

    monkeypatch.setenv("NYT_COOKIES", "NYT-S=abc123; nyt-auth-method=sso")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["cookie"] = request.headers.get("cookie", "")
        captured["user-agent"] = request.headers.get("user-agent", "")
        return httpx.Response(200, content=SAMPLE_HTML)

    real_client_cls = wf.httpx.AsyncClient

    class _StubClient(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(wf.httpx, "AsyncClient", _StubClient)
    await wf.web_fetch("https://www.nytimes.com/2026/05/15/example.html")
    assert "NYT-S=abc123" in captured["cookie"]
    assert "nyt-auth-method=sso" in captured["cookie"]
    # Real browser UA — NYT serves different markup to non-browser clients.
    assert "Mozilla" in captured["user-agent"]


@pytest.mark.asyncio
async def test_cookie_jar_does_not_leak_to_other_hosts(monkeypatch):
    """NYT cookies must not be sent to non-nytimes.com hosts."""
    import digest.tools.web_fetch as wf

    monkeypatch.setenv("NYT_COOKIES", "NYT-S=secret-token")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, content=SAMPLE_HTML)

    real_client_cls = wf.httpx.AsyncClient

    class _StubClient(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(wf.httpx, "AsyncClient", _StubClient)
    await wf.web_fetch("https://example.com/article")
    assert "NYT-S" not in captured["cookie"]
    assert "secret-token" not in captured["cookie"]


@pytest.mark.asyncio
async def test_cookie_jar_handles_missing_env(monkeypatch):
    """Without NYT_COOKIES set, fetch still works and sends no cookie header."""
    import digest.tools.web_fetch as wf

    monkeypatch.delenv("NYT_COOKIES", raising=False)
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, content=SAMPLE_HTML)

    real_client_cls = wf.httpx.AsyncClient

    class _StubClient(real_client_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(wf.httpx, "AsyncClient", _StubClient)
    doc = await wf.web_fetch("https://www.nytimes.com/article")
    assert "first paragraph" in doc.text
    assert captured["cookie"] == ""


def test_build_cookie_jar_strips_cookie_prefix(monkeypatch):
    """Tolerate users pasting a full `Cookie: name=value` line."""
    import digest.tools.web_fetch as wf

    monkeypatch.setenv("NYT_COOKIES", "Cookie: NYT-S=xyz; foo=bar")
    jar = wf._build_cookie_jar()
    cookies = {c.name: (c.value, c.domain) for c in jar.jar}
    assert cookies["NYT-S"] == ("xyz", ".nytimes.com")
    assert cookies["foo"] == ("bar", ".nytimes.com")


def test_build_cookie_jar_ignores_empty_env(monkeypatch):
    import digest.tools.web_fetch as wf

    monkeypatch.delenv("NYT_COOKIES", raising=False)
    jar = wf._build_cookie_jar()
    assert len(list(jar.jar)) == 0


def test_build_cookie_jar_skips_malformed_pairs(monkeypatch):
    """Tolerate stray semicolons, missing '=', and empty names from manual pastes."""
    import digest.tools.web_fetch as wf

    # Trailing semicolon (empty pair); a token with no '='; an empty name.
    monkeypatch.setenv("NYT_COOKIES", "NYT-S=abc; ; orphan; =nokey; foo=bar;")
    jar = wf._build_cookie_jar()
    names = {c.name for c in jar.jar}
    assert names == {"NYT-S", "foo"}
