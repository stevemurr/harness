"""Failure modes from the web-path audit, with deterministic local responses."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from harness.settings import Web
from harness.tools.base import ToolContext, bind
from harness.tools.web import Open, Search, address_error, readable, results_from
from harness.tools.webkit import Rendered
from harness.workspace import Workspace

SETTINGS = replace(Web(), block_private=False)
ARTICLE = (
    "<html><title>Guide</title><article><p>" + "Useful prose. " * 40 + "</p></article></html>"
)
HITS = '<a class="result__a" href="https://example.com/guide">Guide</a>'
GATE = "<html><title>Just a moment...</title><body>Verify you are human</body></html>"


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(paths=Workspace.at(tmp_path))


class Browser:
    available = True

    def __init__(self, html: str, *, delay: float = 0, url: str = "") -> None:
        self.html, self.delay, self.url = html, delay, url
        self.calls = 0
        self.cancelled = False

    async def render(self, url: str, **_options: object) -> Rendered:
        self.calls += 1
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return Rendered(url=self.url or url, title="", html=self.html)


@pytest.mark.parametrize(
    "html",
    ["", "<h1>Service unavailable</h1>", '<div id="links"><a class="renamed">Hit</a></div>'],
)
async def test_unknown_search_markup_is_a_failure(ctx: ToolContext, html: str) -> None:
    result = await bind(
        Search(SETTINGS, httpx.MockTransport(lambda _: httpx.Response(200, text=html)))
    ).call({"query": "test"}, ctx)
    assert not result.ok
    assert "unrecognized" in result.content
    assert "no results" not in result.content


async def test_unrecognized_browser_search_falls_back(ctx: ToolContext) -> None:
    browser = Browser("<h1>Consent required</h1>")
    result = await bind(
        Search(
            SETTINGS,
            httpx.MockTransport(lambda _: httpx.Response(200, text=HITS)),
            webkit=browser,
        )
    ).call({"query": "test"}, ctx)
    assert result.ok and "1. Guide" in result.content


async def test_search_redirects_are_checked_before_connecting(ctx: ToolContext) -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(307, headers={"location": "http://127.0.0.1/secret"})

    result = await bind(
        Search(
            replace(Web(), endpoint="https://93.184.216.34/search"), httpx.MockTransport(answer)
        )
    ).call({"query": "test"}, ctx)
    assert not result.ok and result.refused
    assert seen == ["93.184.216.34"]


@pytest.mark.parametrize(
    "status,method", [(302, "GET"), (303, "GET"), (307, "POST"), (308, "POST")]
)
async def test_search_follows_redirect_method_semantics(
    ctx: ToolContext, status: int, method: str
) -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(status, headers={"location": "/results"})
        return httpx.Response(200, text=HITS)

    result = await bind(Search(SETTINGS, httpx.MockTransport(answer))).call(
        {"query": "test"}, ctx
    )
    assert result.ok and seen[1].method == method
    assert (b"q=test" in seen[1].content) == (method == "POST")


class SlowBody(httpx.AsyncByteStream):
    closed = False

    async def __aiter__(self):
        while True:
            await asyncio.sleep(0.01)
            yield b"x"

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("search", [False, True])
async def test_slow_drip_has_a_total_deadline(ctx: ToolContext, search: bool) -> None:
    body = SlowBody()
    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=body))
    settings = replace(SETTINGS, timeout=0.08)
    tool = Search(settings, transport) if search else Open(settings, transport)
    args = {"query": "test"} if search else {"url": "https://example.com"}
    async with asyncio.timeout(1):
        result = await bind(tool).call(args, ctx)
    assert not result.ok and "timed out" in result.content
    assert body.closed


@pytest.mark.parametrize("search", [False, True])
async def test_oversized_body_is_not_a_successful_partial_page(
    ctx: ToolContext, search: bool
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text=HITS + ARTICLE))
    settings = replace(SETTINGS, max_bytes=80)
    tool = Search(settings, transport) if search else Open(settings, transport)
    args = {"query": "test"} if search else {"url": "https://example.com"}
    result = await bind(tool).call(args, ctx)
    assert not result.ok and "byte limit" in result.content


async def test_search_browser_timeout_leaves_time_for_fetch(ctx: ToolContext) -> None:
    browser = Browser(HITS, delay=5)
    settings = replace(SETTINGS, timeout=0.5, render_timeout=0.03)
    result = await bind(
        Search(
            settings,
            httpx.MockTransport(lambda _: httpx.Response(200, text=HITS)),
            webkit=browser,
        )
    ).call({"query": "test"}, ctx)
    assert result.ok and browser.cancelled


@pytest.mark.parametrize("browser_html", [GATE, "", "<main>Loading...</main>"])
async def test_bot_check_is_not_reported_as_passed_without_content(
    ctx: ToolContext, browser_html: str
) -> None:
    browser = Browser(browser_html)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(403, text=GATE, headers={"cf-mitigated": "challenge"})
    )
    result = await bind(Open(SETTINGS, transport, browser)).call(
        {"url": "https://example.com"}, ctx
    )
    assert not result.ok
    assert "browser passed" not in result.content


@pytest.mark.parametrize(
    "html",
    [
        GATE,
        '<main>Loading...</main><script src="app.js"></script>',
        "<main>Please enable JavaScript to continue.</main>",
    ],
)
async def test_nonempty_shells_and_200_challenges_use_browser(
    ctx: ToolContext, html: str
) -> None:
    browser = Browser(ARTICLE)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    result = await bind(Open(SETTINGS, transport, browser)).call(
        {"url": "https://example.com"}, ctx
    )
    assert result.ok and browser.calls == 1
    assert "Useful prose" in result.content


async def test_short_real_page_does_not_start_a_browser(ctx: ToolContext) -> None:
    browser = Browser(ARTICLE)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            text="<main>Version 2.0 was released today.</main><script>analytics()</script>",
            headers={"content-type": "text/html"},
        )
    )
    result = await bind(Open(SETTINGS, transport, browser)).call(
        {"url": "https://example.com"}, ctx
    )
    assert result.ok and browser.calls == 0


async def test_article_about_bot_checks_is_not_a_challenge(ctx: ToolContext) -> None:
    browser = Browser("")
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            text=ARTICLE.replace(
                "Useful prose.", "How to fix Access Denied and CAPTCHA errors."
            ),
            headers={"content-type": "text/html"},
        )
    )
    result = await bind(Open(SETTINGS, transport, browser)).call(
        {"url": "https://example.com"}, ctx
    )
    assert result.ok and browser.calls == 0


async def test_browser_redirect_keeps_final_url_and_relative_links(ctx: ToolContext) -> None:
    browser = Browser(
        '<main>See <a href="next">Next</a>.</main>', url="https://example.com/docs/guide"
    )
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, text="<html><body></body></html>", headers={"content-type": "text/html"}
        )
    )
    result = await bind(Open(SETTINGS, transport, browser)).call(
        {"url": "https://example.com/start"}, ctx
    )
    assert result.ok
    assert "https://example.com/docs/guide" in result.content
    assert "[Next](https://example.com/docs/next)" in result.content


def test_reader_resolves_links_against_final_url_and_html_base() -> None:
    _, content = readable(
        '<html><base href="../api/"><main>See <a href="next#code">Next</a> '
        + '<a href="javascript:alert(1)">Bad</a></main></html>',
        url="https://example.com/docs/guide",
    )
    assert "[Next](https://example.com/api/next#code)" in content
    assert "javascript:" not in content
    _, anchor = readable(
        '<main><a id="note">Named anchor</a></main>', url="https://example.com"
    )
    assert anchor == "Named anchor"


def test_search_deduplicates_and_rejects_malformed_or_unsafe_urls() -> None:
    urls = [
        "https://example.com/a",
        "https://example.com/a#one",
        "javascript://evil/test",
        "https://[broken",
        "https://duckduckgo.com/y.js",
        "https://notduckduckgo.com/a",
    ]
    results = results_from(
        "".join(f'<a class="result__a" href="{url}">Hit</a>' for url in urls)
    )
    assert [hit.url for hit in results] == [urls[0], urls[-1]]


@pytest.mark.parametrize(
    "url",
    [
        "https://[bad",
        "https://example.com:bad",
        "https://user:password@example.com",
        "http://100.64.0.1/",
    ],
)
async def test_invalid_or_nonpublic_addresses_fail_cleanly(url: str) -> None:
    assert await address_error(url, True)


async def test_search_results_are_framed_as_untrusted(ctx: ToolContext) -> None:
    result = await bind(
        Search(SETTINGS, httpx.MockTransport(lambda _: httpx.Response(200, text=HITS)))
    ).call({"query": "test"}, ctx)
    assert "untrusted text from the web" in result.content


async def test_fetch_and_render_share_one_overall_deadline(ctx: ToolContext) -> None:
    async def answer(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)
        return httpx.Response(
            200, text="<html><body></body></html>", headers={"content-type": "text/html"}
        )

    browser = Browser(ARTICLE, delay=0.3)
    settings = replace(SETTINGS, timeout=0.2, render_timeout=0.4)
    result = await bind(Open(settings, httpx.MockTransport(answer), browser)).call(
        {"url": "https://example.com"}, ctx
    )
    assert not result.ok and "timed out" in result.content
    assert browser.cancelled


async def test_external_cancellation_is_propagated(ctx: ToolContext) -> None:
    browser = Browser(ARTICLE, delay=30)
    tool = bind(Search(SETTINGS, webkit=browser))
    task = asyncio.create_task(tool.call({"query": "test"}, ctx))
    for _ in range(100):
        if browser.calls:
            break
        await asyncio.sleep(0.001)
    assert browser.calls
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert browser.cancelled


async def test_loading_mentioned_in_a_short_article_is_not_a_shell(ctx: ToolContext) -> None:
    browser = Browser("")
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            text="<main>Loading files is faster in version 2.0.</main>",
            headers={"content-type": "text/html"},
        )
    )
    result = await bind(Open(SETTINGS, transport, browser)).call(
        {"url": "https://example.com"}, ctx
    )
    assert result.ok and browser.calls == 0


def test_search_handles_snippets_in_nested_non_anchor_elements() -> None:
    results = results_from(
        HITS + '<div class="result__snippet">First <div>nested</div> last.</div>'
    )
    assert results[0].snippet == "First nested last."


async def test_too_many_elements_fail_before_building_an_unbounded_tree(
    ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import harness.tools.web as web

    monkeypatch.setattr(web, "MAX_ELEMENTS", 20)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            text="<main>" + "<span>x</span>" * 50 + "</main>",
            headers={"content-type": "text/html"},
        )
    )
    result = await bind(Open(SETTINGS, transport)).call({"url": "https://example.com"}, ctx)
    assert not result.ok and "element limit" in result.content


async def test_synchronous_extraction_cannot_report_success_after_deadline(
    ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import harness.tools.web as web

    original = web.readable

    def slow_readable(*args, **kwargs):
        time.sleep(0.05)
        return original(*args, **kwargs)

    monkeypatch.setattr(web, "readable", slow_readable)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html"})
    )
    result = await bind(Open(replace(SETTINGS, timeout=0.02), transport)).call(
        {"url": "https://example.com"}, ctx
    )
    assert not result.ok and "timed out" in result.content
