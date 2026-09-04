"""The research tools, against no network.

Every test here drives either a pure function over a page or an `httpx.MockTransport`. That
is not only for speed: the search half is a scraper, so the day DuckDuckGo changes its markup
these tests must keep passing while the tool stops working, and the fix must be a new page
pasted into `RESULTS_PAGE` below rather than a debugging session against the live web.

The markup in `RESULTS_PAGE` is a trimmed capture of a real response taken 2026-08-31. Its
long tags are wrapped to fit, which changes nothing the parser sees -- whitespace inside a
tag and between words both collapse -- and the awkward parts are kept: the `<b>` around the
matched words, the empty `result__extras` block between a title and its snippet, and a
sponsored result that has to be told apart from a real one.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from harness.settings import Web as WebSettings
from harness.tools.base import ToolContext, bind
from harness.tools.web import (
    Open,
    Search,
    address_error,
    readable,
    results_from,
    unwrap,
)
from harness.workspace import Workspace

RESULTS_PAGE = """
<div id="links" class="results">
  <div class="result results_links results_links_deep web-result ">
    <div class="links_main links_deep result__body">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a"
           href="https://github.com/QwenLM/Qwen3">GitHub -
           QwenLM/<b>Qwen3</b>: the model series ...</a>
      </h2>
      <div class="result__extras"><div class="result__extras__url">
        <a rel="nofollow" href="https://github.com/QwenLM/Qwen3">github.com</a>
      </div></div>
      <a class="result__snippet"
         href="https://github.com/QwenLM/Qwen3">We announce the
         <b>release</b> of <b>Qwen3</b>, the latest addition.</a>
    </div>
  </div>
  <div class="result results_links_deep result--ad">
    <div class="links_main">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a"
           href="//duckduckgo.com/y.js?ad_provider=x&amp;u3=https%3A%2F%2Fsponsor.example"
           >Sponsored thing</a>
      </h2>
      <a class="result__snippet" href="//duckduckgo.com/y.js">Buy the thing.</a>
    </div>
  </div>
  <div class="result results_links results_links_deep web-result ">
    <div class="links_main links_deep result__body">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a"
           href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FQwen&amp;rut=abc"
           >Qwen - Wikipedia</a>
      </h2>
      <a class="result__snippet"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FQwen"
         >An encyclopaedia entry.</a>
    </div>
  </div>
</div>
"""

CHALLENGE_PAGE = """
<div class="anomaly-modal__mask"></div>
<form id="challenge-form" action="//duckduckgo.com/anomaly.js?sv=lite" method="POST"></form>
"""


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(paths=Workspace.at(tmp_path))


def transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------------------
# Parsing results
# --------------------------------------------------------------------------------------


def test_a_result_keeps_its_whole_title_despite_the_bold_around_the_match() -> None:
    """DuckDuckGo wraps the matched words in `<b>`. Ending the capture on that closing tag
    would keep only the words before the first match."""
    results = results_from(RESULTS_PAGE)

    assert results[0].title == "GitHub - QwenLM/Qwen3: the model series ..."
    assert results[0].snippet == "We announce the release of Qwen3, the latest addition."


def test_sponsored_and_internal_links_are_not_results() -> None:
    """Ads leave through `y.js` on duckduckgo.com, so filtering by host removes them
    without depending on a class name that can be renamed."""
    urls = [result.url for result in results_from(RESULTS_PAGE)]

    assert urls == ["https://github.com/QwenLM/Qwen3", "https://en.wikipedia.org/wiki/Qwen"]


def test_a_redirected_result_is_unwrapped_to_its_destination() -> None:
    """Both forms are served depending on region: the target directly, or wrapped in
    `/l/?uddg=`. A tool that handles one returns duckduckgo.com as a result on the other."""
    assert (
        unwrap("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x")
        == "https://example.com/a"
    )
    assert unwrap("https://example.com/a") == "https://example.com/a"
    assert unwrap("") == ""


def test_a_snippet_belongs_to_the_title_above_it() -> None:
    results = results_from(RESULTS_PAGE)

    assert results[1].url == "https://en.wikipedia.org/wiki/Qwen"
    assert results[1].snippet == "An encyclopaedia entry."


def test_markup_that_no_longer_matches_yields_nothing_rather_than_raising() -> None:
    """The failure mode this scraper will actually have. It must be an empty list the tool
    can report, not an exception in the middle of a run."""
    assert results_from("<div class='result'><a href='/x'>renamed</a></div>") == []


# --------------------------------------------------------------------------------------
# Reader mode
# --------------------------------------------------------------------------------------

ARTICLE_PAGE = """
<html><head><title>How wrapping works</title>
<style>.a{color:red}</style><script>var x = "not text";</script></head>
<body>
  <nav><a href="/a">Home</a><a href="/b">Docs</a><a href="/c">Blog</a></nav>
  <article>
    <h1>How wrapping works</h1>
    <p>The first paragraph, which is <b>emphatic</b> in places.</p>
    <h2>Details</h2>
    <ul><li>One thing</li><li>Another thing</li></ul>
    <pre><code>def f():
    return 1</code></pre>
    <p>See <a href="https://example.com/next">the next page</a> for more.</p>
  </article>
  <footer><a href="/privacy">Privacy</a></footer>
</body></html>
"""


def test_reader_mode_keeps_the_article_and_drops_the_furniture() -> None:
    title, text = readable(ARTICLE_PAGE)

    assert title == "How wrapping works"
    assert "The first paragraph, which is emphatic in places." in text
    assert "# How wrapping works" in text
    assert "## Details" in text
    assert "- One thing" in text
    # The navigation, the footer, the stylesheet and the script are not content.
    assert "Home" not in text and "Privacy" not in text
    assert "color:red" not in text and "not text" not in text


def test_code_blocks_keep_their_indentation() -> None:
    """A documentation example that has been through whitespace collapsing is an example
    that cannot be copied."""
    _, text = readable(ARTICLE_PAGE)

    assert "```\ndef f():\n    return 1\n```" in text


def test_links_keep_their_urls_so_the_tool_composes_with_itself() -> None:
    _, text = readable(ARTICLE_PAGE)

    assert "[the next page](https://example.com/next)" in text


DENSE_PAGE = """
<html><body>
  <div id="sidebar">
    <a href="/1">Alpha</a><a href="/2">Beta</a><a href="/3">Gamma</a>
    <a href="/4">Delta</a><a href="/5">Epsilon</a><a href="/6">Zeta</a>
  </div>
  <div id="content">
    <p>A paragraph of real prose that runs on for a while and says something.</p>
    <p>A second paragraph, also prose, with one <a href="/x">link</a> in it.</p>
  </div>
</body></html>
"""


def test_without_an_article_tag_the_prose_beats_the_link_list() -> None:
    """The one idea worth keeping from readability algorithms: navigation is short text in
    many links, prose is long text in few."""
    _, text = readable(DENSE_PAGE)

    assert "A paragraph of real prose" in text
    assert "Epsilon" not in text


def test_an_unclosed_tag_does_not_swallow_the_rest_of_the_page() -> None:
    """Real pages leave tags open. Popping the stack unconditionally on a stray `</div>`
    would end the article early."""
    _, text = readable(
        "<html><body><article><p>one<p>two</div><p>three</article></body></html>"
    )

    assert "one" in text and "two" in text and "three" in text


def test_a_page_with_nothing_in_it_reads_as_empty_rather_than_failing() -> None:
    assert readable("<html><body><script>x=1</script></body></html>")[1] == ""


# --------------------------------------------------------------------------------------
# Where it will not go
# --------------------------------------------------------------------------------------


async def test_loopback_and_private_addresses_are_refused() -> None:
    """The harness's own server listens on 127.0.0.1:8080, so this is not hypothetical."""
    assert "on this machine" in await address_error("http://127.0.0.1:8080/x", True)
    assert "on this machine" in await address_error("http://192.168.1.237:4000/v1", True)
    assert "on this machine" in await address_error("http://169.254.169.254/latest", True)


async def test_only_http_and_https_are_fetched() -> None:
    assert "only http and https" in await address_error("file:///etc/passwd", True)
    assert "only http and https" in await address_error("ftp://example.com/x", True)


async def test_a_public_address_passes() -> None:
    """A literal address, so the check runs without asking DNS anything."""
    assert await address_error("http://93.184.216.34/page", True) == ""


async def test_turning_the_guard_off_skips_resolution_entirely() -> None:
    """The field exists so someone who genuinely wants an agent reading their intranet has
    to say so. When they have, nothing is resolved and nothing is refused."""
    assert await address_error("http://127.0.0.1:8080/x", False) == ""


# --------------------------------------------------------------------------------------
# The tools end to end, against a fake transport
# --------------------------------------------------------------------------------------

OPEN_ANYWHERE = replace(WebSettings(), block_private=False)


async def test_search_returns_ranked_results_with_their_urls(ctx: ToolContext) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=RESULTS_PAGE)

    result = await bind(Search(transport=transport(handler))).call({"query": "qwen3"}, ctx)

    assert result.ok
    assert "1. GitHub - QwenLM/Qwen3" in result.content
    assert "https://en.wikipedia.org/wiki/Qwen" in result.content
    # POST, because a GET of the same query is answered with a challenge page.
    assert seen[0].method == "POST"
    assert b"q=qwen3" in seen[0].content


async def test_search_honours_the_result_count_it_was_asked_for(ctx: ToolContext) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=RESULTS_PAGE)

    result = await bind(Search(transport=transport(handler))).call(
        {"query": "qwen3", "max_results": 1}, ctx
    )

    assert "1. GitHub" in result.content
    assert "wikipedia" not in result.content


async def test_being_rate_limited_does_not_read_as_no_results(ctx: ToolContext) -> None:
    """The two outcomes look identical in a result count and mean opposite things: one is
    "nothing matched", the other is "the engine refused this machine"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, text=CHALLENGE_PAGE)

    result = await bind(Search(transport=transport(handler))).call({"query": "qwen3"}, ctx)

    assert not result.ok
    assert "rate-limiting" in result.content


async def test_a_query_that_matches_nothing_is_a_successful_search(ctx: ToolContext) -> None:
    """On the reasoning `shell.py` gives for a non-zero exit: the tool did its job and the
    answer was negative. Reporting it as a failure would count towards the loop's refusal
    cap for a model asking a reasonable question."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<div id='links' class='results'></div>")

    result = await bind(Search(transport=transport(handler))).call({"query": "zxqw"}, ctx)

    assert result.ok
    assert "no results" in result.content


async def test_a_blank_query_is_refused_rather_than_searched_for(ctx: ToolContext) -> None:
    result = await bind(Search()).call({"query": "   "}, ctx)

    assert result.refused and not result.ok


async def test_open_url_returns_the_page_as_readable_text(ctx: ToolContext) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=ARTICLE_PAGE, headers={"content-type": "text/html; charset=utf-8"}
        )

    opener = Open(OPEN_ANYWHERE, transport(handler))
    result = await bind(opener).call({"url": "https://example.com/a"}, ctx)

    assert result.ok
    assert "How wrapping works" in result.content
    assert "untrusted text from the web" in result.content
    assert "The first paragraph" in result.content


async def test_a_redirect_into_the_private_network_is_refused(ctx: ToolContext) -> None:
    """The reason redirects are followed by hand. A check made only on the URL the model
    typed passes a public address that 302s to localhost."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/watch"})
        return httpx.Response(200, text="<html><body><p>secret</p></body></html>")

    result = await bind(Open(transport=transport(handler))).call(
        {"url": "http://93.184.216.34/start"}, ctx
    )

    assert result.refused and not result.ok
    assert "on this machine" in result.content
    assert "secret" not in result.content


async def test_a_redirect_to_another_public_page_is_followed(ctx: ToolContext) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "/moved"})
        return httpx.Response(
            200,
            text="<html><body><article><p>arrived</p></article></body></html>",
            headers={"content-type": "text/html"},
        )

    result = await bind(Open(OPEN_ANYWHERE, transport(handler))).call(
        {"url": "https://example.com/start"}, ctx
    )

    assert result.ok and "arrived" in result.content


async def test_a_pdf_says_it_cannot_be_read_rather_than_returning_bytes(
    ctx: ToolContext,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"%PDF-1.7 ...", headers={"content-type": "application/pdf"}
        )

    result = await bind(Open(OPEN_ANYWHERE, transport(handler))).call(
        {"url": "https://example.com/a.pdf"}, ctx
    )

    assert not result.ok
    assert "cannot read" in result.content


async def test_plain_text_is_returned_without_being_parsed_as_html(
    ctx: ToolContext,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="a < b and c > d", headers={"content-type": "text/plain"}
        )

    result = await bind(Open(OPEN_ANYWHERE, transport(handler))).call(
        {"url": "https://example.com/a.txt"}, ctx
    )

    assert result.ok and "a < b and c > d" in result.content


async def test_a_long_page_is_cut_and_says_so(ctx: ToolContext) -> None:
    body = "".join(f"<p>{'paragraph ' * 20}</p>" for _ in range(50))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=f"<html><body><article>{body}</article></body></html>",
            headers={"content-type": "text/html"},
        )

    result = await bind(Open(OPEN_ANYWHERE, transport(handler))).call(
        {"url": "https://example.com/long", "max_chars": 500}, ctx
    )

    assert result.ok
    assert "[cut here: the page is longer than 500 characters." in result.content
    assert "start=" in result.content


async def test_a_page_with_no_text_says_which_kind_of_empty_it_is(ctx: ToolContext) -> None:
    """A JavaScript-built page is the common case and the model can act on knowing it --
    by opening a different URL rather than trying the same one again."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><body><div id='root'></div><script>render()</script></body></html>",
            headers={"content-type": "text/html"},
        )

    result = await bind(Open(OPEN_ANYWHERE, transport(handler))).call(
        {"url": "https://example.com/app"}, ctx
    )

    assert not result.ok
    assert "JavaScript" in result.content


async def test_an_unreachable_host_is_a_failure_not_a_crash(ctx: ToolContext) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    result = await bind(Open(OPEN_ANYWHERE, transport(handler))).call(
        {"url": "https://example.com/a"}, ctx
    )

    assert not result.ok and "could not fetch" in result.content


async def test_an_empty_main_does_not_beat_the_prose_beside_it() -> None:
    """Sites that render into an empty `<main>` and leave the server-rendered copy beside
    it are common. Believing the tag alone returns "no readable text" for a page that
    plainly has some."""
    _, text = readable(
        "<html><body><main id='app'></main>"
        + "<div id='real'><p>"
        + "Actual prose that goes on for a while. " * 8
        + "</p></div>"
        + "</body></html>"
    )

    assert "Actual prose" in text


async def test_a_malformed_port_is_refused_rather_than_raising() -> None:
    """`urlsplit` parses `http://x:80a/` happily and raises only when something asks for
    the number, which would otherwise be an exception escaping the tool mid-run."""
    assert "usable port" in await address_error("http://example.com:80a/x", True)


async def test_a_page_served_without_a_content_type_is_still_read(
    ctx: ToolContext,
) -> None:
    """Refusing those would be refusing a readable page on a technicality."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html><body><article><p>served bare</p></article></body></html>",
            headers={"content-type": ""},
        )

    result = await bind(Open(OPEN_ANYWHERE, transport(handler))).call(
        {"url": "https://example.com/bare"}, ctx
    )

    assert result.ok and "served bare" in result.content


# -- pages written for readers without JavaScript ---------------------------------------


DISCOURSE_PAGE = """<!doctype html><html><head><title>Hanging tasks - Swift Forums</title>
<script>window.preload = {"huge": "json"}</script></head>
<body class="crawler">
<noscript><style>#d-splash { display: none; }</style></noscript>
<div id="ember-shell"></div>
<noscript data-path="/t/hanging-tasks/1">
  <header><a href="/">Swift Forums</a></header>
  <div id="main-outlet" class="wrap" role="main">
    <div id="topic-title"><h1><a href="/t/hanging-tasks/1">Hanging tasks</a></h1></div>
    <div class="post" itemprop="text"><p>Before I describe my problem in detail, I would
    like to summarize the situation, because it matters for understanding it.</p>
    <ul><li>240 test cases</li><li>All pass with four workers on the Mac</li></ul></div>
    <div class="post" itemprop="text"><p>Have you tried a fresh URLSession per test? The
    shared one keeps connections alive across tests and that can serialise them.</p></div>
  </div>
</noscript>
</body></html>"""


def test_a_discourse_topic_is_read_from_its_noscript_block() -> None:
    """The tool does not run JavaScript, which makes it the reader `noscript` is written
    for. Dropping it read every Discourse thread as empty."""
    title, text = readable(DISCOURSE_PAGE)

    assert title.startswith("Hanging tasks")
    assert "summarize the situation" in text
    assert "fresh URLSession per test" in text
    assert "240 test cases" in text
    assert "d-splash" not in text  # the style inside the other noscript is still dropped


# -- the browser, as a fallback ----------------------------------------------------------


SHELL_PAGE = (
    "<!doctype html><html><head><title>App</title></head>"
    + '<body><div id="root"></div><script src="/app.js"></script></body></html>'
)


class _FakeRenderer:
    def __init__(self, html: str | None = None, error: Exception | None = None) -> None:
        self.html, self.error = html, error
        self.rendered: list[str] = []
        self.closed = False

    async def render(self, url: str) -> str:
        self.rendered.append(url)
        if self.error is not None:
            raise self.error
        return self.html or ""

    async def aclose(self) -> None:
        self.closed = True


def _shell_transport() -> httpx.AsyncBaseTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SHELL_PAGE, headers={"content-type": "text/html"})

    return transport(handler)


async def test_an_empty_shell_is_rendered_and_read(ctx: ToolContext) -> None:
    renderer = _FakeRenderer(html=ARTICLE_PAGE)
    opener = Open(OPEN_ANYWHERE, _shell_transport(), renderer)

    result = await bind(opener).call({"url": "https://example.com/app"}, ctx)

    assert result.ok
    assert renderer.rendered == ["https://example.com/app"]
    assert "How wrapping works" in result.content
    assert "rendered in a browser" in result.content


async def test_a_page_with_text_is_never_rendered(ctx: ToolContext) -> None:
    """The fetch is the common path. A browser is reached for only when it reads as empty."""
    renderer = _FakeRenderer(html=ARTICLE_PAGE)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=ARTICLE_PAGE, headers={"content-type": "text/html"})

    opener = Open(OPEN_ANYWHERE, transport(handler), renderer)
    result = await bind(opener).call({"url": "https://example.com/a"}, ctx)

    assert result.ok and renderer.rendered == []
    assert "rendered in a browser" not in result.content


async def test_without_a_browser_the_tool_says_how_to_get_one(ctx: ToolContext) -> None:
    from harness.tools.browser import INSTALL, RenderUnavailable

    renderer = _FakeRenderer(
        error=RenderUnavailable(f"no browser is installed. Install one with: {INSTALL}")
    )
    opener = Open(OPEN_ANYWHERE, _shell_transport(), renderer)

    result = await bind(opener).call({"url": "https://example.com/app"}, ctx)

    assert not result.ok and not result.refused
    assert "install-browser" in result.content


async def test_a_render_that_still_reads_as_empty_says_so(ctx: ToolContext) -> None:
    renderer = _FakeRenderer(html=SHELL_PAGE)
    opener = Open(OPEN_ANYWHERE, _shell_transport(), renderer)

    result = await bind(opener).call({"url": "https://example.com/app"}, ctx)

    assert not result.ok
    assert "even after running its JavaScript" in result.content


async def test_rendering_can_be_switched_off(ctx: ToolContext) -> None:
    from dataclasses import replace

    renderer = _FakeRenderer(html=ARTICLE_PAGE)
    opener = Open(replace(OPEN_ANYWHERE, render=False), _shell_transport(), renderer)

    result = await bind(opener).call({"url": "https://example.com/app"}, ctx)

    assert not result.ok and renderer.rendered == []
    assert "rendering is not available" in result.content


# -- a bot check, a cut page read on, and a GitHub file -----------------------------------


async def test_the_fetch_sends_a_browsers_navigation_headers(ctx: ToolContext) -> None:
    """Measured 2026-09-03: a Cloudflare-fronted page answered 403 to a browser user agent
    with a script-shaped header set, and 200 to the same client sending what a browser
    sends on navigation. The client hints have to agree with the user agent."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, text=ARTICLE_PAGE, headers={"content-type": "text/html"})

    result = await bind(Open(OPEN_ANYWHERE, transport(handler))).call(
        {"url": "https://example.com/a"}, ctx
    )

    assert result.ok
    assert seen["user-agent"].startswith("Mozilla/5.0") and "Chrome/" in seen["user-agent"]
    assert seen["sec-fetch-mode"] == "navigate" and seen["sec-fetch-dest"] == "document"
    assert seen["upgrade-insecure-requests"] == "1"
    major = seen["user-agent"].split("Chrome/")[1].split(".")[0]
    assert f'v="{major}"' in seen["sec-ch-ua"] and seen["sec-ch-ua-platform"] == '"macOS"'
    assert "text/html" in seen["accept"]


def test_a_user_agent_that_is_not_chrome_sends_no_chrome_hints() -> None:
    from harness.tools.addresses import navigation_headers

    safari = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"
    )
    headers = navigation_headers(safari, "de-DE,de;q=0.9")
    assert "sec-ch-ua" not in headers and headers["Accept-Language"] == "de-DE,de;q=0.9"


BOT_CHECK_PAGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
    + '<body><div id="challenge-platform">Enable JavaScript and cookies to continue</div>'
    + "</body></html>"
)


async def test_a_bot_check_is_rendered_in_the_browser_and_says_so(ctx: ToolContext) -> None:
    """Medium behind Cloudflare: 403 with a challenge to the fetch, the article to the
    browser. A 403 that is a bot check is a page that needs a browser, not a failure."""
    renderer = _FakeRenderer(html=ARTICLE_PAGE)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text=BOT_CHECK_PAGE,
            headers={"content-type": "text/html", "cf-mitigated": "challenge"},
        )

    result = await bind(Open(OPEN_ANYWHERE, transport(handler), renderer)).call(
        {"url": "https://example.com/article"}, ctx
    )

    assert result.ok, result.content
    assert renderer.rendered == ["https://example.com/article"]
    assert "bot check, which the browser passed" in result.content
    assert "How wrapping works" in result.content


async def test_a_bot_check_without_a_browser_is_a_failure_that_says_why(
    ctx: ToolContext,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=BOT_CHECK_PAGE, headers={"content-type": "text/html"})

    result = await bind(Open(OPEN_ANYWHERE, transport(handler), None)).call(
        {"url": "https://example.com/article"}, ctx
    )

    assert not result.ok and not result.refused
    assert "answered 403 with a bot check" in result.content
    assert "not available" in result.content


async def test_a_plain_403_is_still_a_failure_and_never_rendered(ctx: ToolContext) -> None:
    renderer = _FakeRenderer(html=ARTICLE_PAGE)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html><body><h1>Forbidden</h1></body></html>")

    result = await bind(Open(OPEN_ANYWHERE, transport(handler), renderer)).call(
        {"url": "https://example.com/private"}, ctx
    )

    assert not result.ok and "answered 403" in result.content
    assert renderer.rendered == []


def test_only_a_refusing_status_can_be_a_challenge() -> None:
    from harness.tools.web import challenged

    assert challenged(403, "challenge", "")
    assert challenged(503, "", BOT_CHECK_PAGE)
    assert not challenged(200, "", BOT_CHECK_PAGE)  # a page about challenges is a page
    assert not challenged(403, "", "<h1>Forbidden</h1>")


async def test_a_cut_page_says_which_start_to_read_on_from(ctx: ToolContext) -> None:
    """The run that motivated this opened a 64k-character page, was cut at 20k, and had no
    way to read the part that held the answer -- so it searched five more times and
    guessed. Measured 2026-09-03."""
    body = "".join(f"<p>paragraph {n} " + "word " * 20 + "</p>" for n in range(60))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=f"<html><body><article>{body}</article></body></html>",
            headers={"content-type": "text/html"},
        )

    opener = bind(Open(OPEN_ANYWHERE, transport(handler)))
    first = await opener.call({"url": "https://example.com/long", "max_chars": 800}, ctx)
    assert first.ok
    assert "Call open_url again with start=" in first.content
    start = int(first.content.split("start=")[1].split()[0])
    assert 600 <= start <= 800

    second = await opener.call(
        {"url": "https://example.com/long", "max_chars": 800, "start": start}, ctx
    )
    assert second.ok and f"(characters {start}-" in second.content
    assert "paragraph 0 " not in second.content  # past the first page of it
    past = await opener.call({"url": "https://example.com/long", "start": 10**6}, ctx)
    assert past.ok and "past the end" in past.content


async def test_a_github_blob_url_is_read_as_the_raw_file(ctx: ToolContext) -> None:
    from harness.tools.web import raw_github

    assert (
        raw_github("https://github.com/o/r/blob/main/Docs/Spec.md")
        == "https://raw.githubusercontent.com/o/r/main/Docs/Spec.md"
    )
    assert raw_github("https://github.com/o/r/blob/main/a.md#L10").endswith("#L10")
    assert raw_github("https://github.com/o/r/issues/731") == "https://github.com/o/r/issues/731"

    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(
            200, text="# Spec\n\nthe file itself\n", headers={"content-type": "text/plain"}
        )

    result = await bind(Open(OPEN_ANYWHERE, transport(handler))).call(
        {"url": "https://github.com/o/r/blob/main/Docs/Spec.md"}, ctx
    )
    assert result.ok and asked == ["https://raw.githubusercontent.com/o/r/main/Docs/Spec.md"]
    assert "the file itself" in result.content
