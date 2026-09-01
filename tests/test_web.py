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
from harness.tools.base import ToolContext
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

    result = await Search(transport=transport(handler)).run({"query": "qwen3"}, ctx)

    assert result.ok
    assert "1. GitHub - QwenLM/Qwen3" in result.content
    assert "https://en.wikipedia.org/wiki/Qwen" in result.content
    # POST, because a GET of the same query is answered with a challenge page.
    assert seen[0].method == "POST"
    assert b"q=qwen3" in seen[0].content


async def test_search_honours_the_result_count_it_was_asked_for(ctx: ToolContext) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=RESULTS_PAGE)

    result = await Search(transport=transport(handler)).run(
        {"query": "qwen3", "max_results": 1}, ctx
    )

    assert "1. GitHub" in result.content
    assert "wikipedia" not in result.content


async def test_being_rate_limited_does_not_read_as_no_results(ctx: ToolContext) -> None:
    """The two outcomes look identical in a result count and mean opposite things: one is
    "nothing matched", the other is "the engine refused this machine"."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, text=CHALLENGE_PAGE)

    result = await Search(transport=transport(handler)).run({"query": "qwen3"}, ctx)

    assert not result.ok
    assert "rate-limiting" in result.content


async def test_a_query_that_matches_nothing_is_a_successful_search(ctx: ToolContext) -> None:
    """On the reasoning `shell.py` gives for a non-zero exit: the tool did its job and the
    answer was negative. Reporting it as a failure would count towards the loop's refusal
    cap for a model asking a reasonable question."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<div id='links' class='results'></div>")

    result = await Search(transport=transport(handler)).run({"query": "zxqw"}, ctx)

    assert result.ok
    assert "no results" in result.content


async def test_a_blank_query_is_refused_rather_than_searched_for(ctx: ToolContext) -> None:
    result = await Search().run({"query": "   "}, ctx)

    assert result.refused and not result.ok


async def test_open_url_returns_the_page_as_readable_text(ctx: ToolContext) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=ARTICLE_PAGE, headers={"content-type": "text/html; charset=utf-8"}
        )

    opener = Open(OPEN_ANYWHERE, transport(handler))
    result = await opener.run({"url": "https://example.com/a"}, ctx)

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

    result = await Open(transport=transport(handler)).run(
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

    result = await Open(OPEN_ANYWHERE, transport(handler)).run(
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

    result = await Open(OPEN_ANYWHERE, transport(handler)).run(
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

    result = await Open(OPEN_ANYWHERE, transport(handler)).run(
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

    result = await Open(OPEN_ANYWHERE, transport(handler)).run(
        {"url": "https://example.com/long", "max_chars": 500}, ctx
    )

    assert result.ok
    assert "[cut here: the page is longer than 500 characters]" in result.content


async def test_a_page_with_no_text_says_which_kind_of_empty_it_is(ctx: ToolContext) -> None:
    """A JavaScript-built page is the common case and the model can act on knowing it --
    by opening a different URL rather than trying the same one again."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><body><div id='root'></div><script>render()</script></body></html>",
            headers={"content-type": "text/html"},
        )

    result = await Open(OPEN_ANYWHERE, transport(handler)).run(
        {"url": "https://example.com/app"}, ctx
    )

    assert not result.ok
    assert "JavaScript" in result.content


async def test_an_unreachable_host_is_a_failure_not_a_crash(ctx: ToolContext) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    result = await Open(OPEN_ANYWHERE, transport(handler)).run(
        {"url": "https://example.com/a"}, ctx
    )

    assert not result.ok and "could not fetch" in result.content


async def test_an_empty_main_does_not_beat_the_prose_beside_it() -> None:
    """Sites that render into an empty `<main>` and leave the server-rendered copy beside
    it are common. Believing the tag alone returns "no readable text" for a page that
    plainly has some."""
    _, text = readable(
        "<html><body><main id='app'></main>"
        "<div id='real'><p>" + "Actual prose that goes on for a while. " * 8 + "</p></div>"
        "</body></html>"
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

    result = await Open(OPEN_ANYWHERE, transport(handler)).run(
        {"url": "https://example.com/bare"}, ctx
    )

    assert result.ok and "served bare" in result.content
