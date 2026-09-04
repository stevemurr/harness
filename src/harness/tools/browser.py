"""Rendering a page the way a browser would, for the pages that need it.

`open_url` fetches HTML and reads it. Most pages are readable that way; some ship an empty
shell and build everything in JavaScript, and for those the fetch reads as nothing. This
is the fallback for that case, and only that case: a headless Chromium, driven through
Playwright, that loads the page, waits for it to settle, and hands back the DOM as HTML
for the same reader-mode extractor to read.

The same browser takes a screenshot, for `screenshot`: the page laid out at a viewport,
with its stylesheets and images this time, as a PNG and as a reading -- title, document
size against the viewport, headings, landmarks, body colours, console errors, requests
that failed. The reading is what a text-only model acts on; the PNG is for a person.

Three rules, because a browser is a much larger thing than an HTTP client:

  * **Every request the page makes is checked, not only the one the model typed.** A page
    can load a subresource from, or redirect to, an address on this machine or its
    network, and a browser that followed it would undo the guard the fetch path keeps.
    Playwright's request interception runs `address_error` on each one and refuses the
    ones it refuses. A capture of a file in the working folder may load files beside it
    and nothing else on disk.
  * **It is bounded.** A render loads no images, fonts or media; a capture loads them,
    because a screenshot without them is not the page. Downloads are refused, there is
    no profile, one browser serves the whole process and starts only when first needed,
    and a page gets `render_timeout` seconds to settle before it is read as it is.
  * **It is optional.** Playwright and its Chromium are an extra, and a harness without
    them says so in the tool result, with the two commands that install them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import urllib.parse
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from harness.settings import Web as WebSettings
from harness.types import as_dict, as_int, as_list, as_str

if TYPE_CHECKING:
    from playwright.async_api import Browser, Page, Playwright, Route

log = logging.getLogger(__name__)

INSTALL = "uv sync --extra browser && uv run harness install-browser"

#: Resource kinds a render does not need. Skipping them is most of the speed.
SKIPPED = frozenset({"image", "media", "font", "stylesheet", "manifest", "texttrack"})

#: How many console errors and failed requests a capture keeps. Enough to see a pattern;
#: a page that logs in a loop would otherwise return the loop.
KEPT = 8


class RenderUnavailable(Exception):
    """There is no browser to render with. The message says how to get one."""


class RenderFailed(Exception):
    """The browser could not load the page. The message says why."""


@dataclass(frozen=True, slots=True)
class Capture:
    """One screenshot: the image, and the page read as facts a model can act on."""

    png: bytes
    url: str
    title: str
    viewport: tuple[int, int]
    #: The document's scrollable size. Wider than the viewport means a horizontal
    #: scrollbar, which on a phone-sized viewport is the layout bug a person sees first.
    document: tuple[int, int]
    #: `h1: Hello`, in document order, the first twenty.
    headings: tuple[str, ...] = ()
    #: `header=1 nav=1 main=1 footer=1 ...`, one count per landmark element.
    landmarks: str = ""
    links: int = 0
    images: int = 0
    images_without_alt: int = 0
    #: The body's computed font, size, colour and background, as the browser has them.
    font: str = ""
    font_size: str = ""
    color: str = ""
    background: str = ""
    text_chars: int = 0
    console_errors: tuple[str, ...] = ()
    failed_requests: tuple[str, ...] = ()


class Renderer(Protocol):
    """What `open_url` falls back to and `screenshot` uses. Two methods, and the way to
    hang up."""

    async def render(self, url: str) -> str:
        """The page's HTML after it has run, or raise `RenderUnavailable` / `RenderFailed`."""
        ...

    async def capture(
        self,
        url: str,
        *,
        width: int = 1280,
        height: int = 900,
        full_page: bool = False,
        dark: bool = False,
        files_under: Path | None = None,
    ) -> Capture:
        """The page at that viewport, as a PNG and a reading. `files_under` is the one
        folder a `file://` page may load from; without it, no file is loaded at all."""
        ...

    async def aclose(self) -> None: ...


def new_renderer(settings: WebSettings | None = None) -> Renderer:
    return _Chromium(settings or WebSettings())


def file_error(url: str, under: Path | None) -> str:
    """Why this `file://` URL must not be loaded, or an empty string.

    A capture of a page in the working folder needs its stylesheet beside it, so files
    under that one folder are allowed. Nothing else on the disk is: a page that links
    `file:///etc/passwd` into an `<iframe>` would otherwise put it in the picture.
    """
    if under is None:
        return "no file may be loaded: this page is not in the working folder"
    parts = urllib.parse.urlsplit(url)
    if parts.netloc not in ("", "localhost"):
        return f"{url!r} names a host; only local files are loaded"
    try:
        path = Path(urllib.parse.unquote(parts.path)).resolve()
    except (OSError, ValueError) as exc:
        return f"{url!r} is not a usable path ({exc})"
    if not (path == under or path.is_relative_to(under)):
        return f"{path} is outside {under}; only files in the working folder are loaded"
    return ""


#: The reading, taken in the page. Numbers and short strings only, so the result stays a
#: few lines whatever the page is.
_READING = """() => {
  const all = (selector) => Array.from(document.querySelectorAll(selector));
  const text = (node) => (node.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
  const scroller = document.scrollingElement || document.documentElement;
  const body = getComputedStyle(document.body);
  const landmarks = ['header', 'nav', 'main', 'footer', 'aside', 'section', 'article', 'form']
    .map((tag) => tag + '=' + all(tag).length).join(' ');
  return {
    title: document.title,
    document: [scroller.scrollWidth, scroller.scrollHeight],
    headings: all('h1, h2, h3').slice(0, 20)
      .map((h) => h.tagName.toLowerCase() + ': ' + text(h)),
    landmarks: landmarks,
    links: all('a[href]').length,
    images: all('img').length,
    images_without_alt: all('img:not([alt])').length,
    font: body.fontFamily,
    font_size: body.fontSize,
    color: body.color,
    background: body.backgroundColor,
    text_chars: (document.body.innerText || '').length,
  };
}"""


@dataclass
class _Chromium:
    settings: WebSettings
    _playwright: Playwright | None = field(default=None, repr=False)
    _browser: Browser | None = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def render(self, url: str) -> str:
        browser = await self._launched()
        context = await browser.new_context(
            user_agent=self.settings.user_agent,
            locale=self.settings.accept_language.split(",")[0],
            java_script_enabled=True,
            accept_downloads=False,
            viewport={"width": 1280, "height": 900},
        )
        try:
            page = await context.new_page()
            _ = await page.route("**/*", self._guard(url, files_under=None, skip=SKIPPED))
            await self._settle(page, url)
            return await page.content()
        finally:
            await context.close()

    async def capture(
        self,
        url: str,
        *,
        width: int = 1280,
        height: int = 900,
        full_page: bool = False,
        dark: bool = False,
        files_under: Path | None = None,
    ) -> Capture:
        browser = await self._launched()
        context = await browser.new_context(
            user_agent=self.settings.user_agent,
            locale=self.settings.accept_language.split(",")[0],
            java_script_enabled=True,
            accept_downloads=False,
            viewport={"width": width, "height": height},
            color_scheme="dark" if dark else "light",
        )
        try:
            page = await context.new_page()
            errors: list[str] = []
            failed: list[str] = []
            _watch(page, errors, failed)
            _ = await page.route("**/*", self._guard(url, files_under=files_under, skip=()))
            await self._settle(page, url)
            reading = as_dict(cast("object", await page.evaluate(_READING)))
            png = await page.screenshot(type="png", full_page=full_page)
            document = [as_int(n) for n in as_list(reading.get("document"))]
            return Capture(
                png=png,
                url=page.url,
                title=as_str(reading.get("title")),
                viewport=(width, height),
                document=(document[0], document[1]) if len(document) == 2 else (0, 0),
                headings=tuple(as_str(h) for h in as_list(reading.get("headings"))),
                landmarks=as_str(reading.get("landmarks")),
                links=as_int(reading.get("links")),
                images=as_int(reading.get("images")),
                images_without_alt=as_int(reading.get("images_without_alt")),
                font=as_str(reading.get("font")),
                font_size=as_str(reading.get("font_size")),
                color=as_str(reading.get("color")),
                background=as_str(reading.get("background")),
                text_chars=as_int(reading.get("text_chars")),
                console_errors=tuple(errors[:KEPT]),
                failed_requests=tuple(failed[:KEPT]),
            )
        finally:
            await context.close()

    def _guard(
        self, url: str, *, files_under: Path | None, skip: frozenset[str] | tuple[()]
    ) -> Callable[[Route], Coroutine[None, None, None]]:
        """The rule for every request the page makes, as a route handler."""
        from harness.tools.addresses import address_error

        async def guard(route: Route) -> None:
            request = route.request
            if request.resource_type in skip:
                await route.abort()
                return
            if request.url.startswith("file:"):
                refusal = file_error(request.url, files_under)
            elif request.url.startswith(("data:", "blob:", "about:")):
                refusal = ""
            else:
                refusal = await address_error(request.url, self.settings.block_private)
            if refusal:
                log.info("render of %s refused a request: %s", url, refusal)
                await route.abort()
                return
            await route.continue_()

        return guard

    async def _settle(self, page: Page, url: str) -> None:
        """Load the page and give it its time. Settled, or as settled as it gets: a page
        that polls forever never reaches "networkidle", and the DOM it has by then is
        the answer."""
        from playwright.async_api import Error as PlaywrightError

        timeout = int(self.settings.render_timeout * 1000)
        try:
            _ = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            with contextlib.suppress(PlaywrightError):
                await page.wait_for_load_state("networkidle", timeout=timeout)
        except PlaywrightError as exc:
            raise RenderFailed(f"the browser could not load {url}: {exc}") from exc

    async def _launched(self) -> Browser:
        async with self._lock:
            if self._browser is not None:
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RenderUnavailable(
                    f"no browser is installed to run it. Install one with: {INSTALL}"
                ) from exc
            try:
                playwright = await async_playwright().start()
                browser = await playwright.chromium.launch(headless=True)
            except Exception as exc:
                raise RenderUnavailable(
                    "the browser could not start "
                    + f"({str(exc).splitlines()[0][:160]}). Install it with: {INSTALL}"
                ) from exc
            self._playwright, self._browser = playwright, browser
            return browser

    async def aclose(self) -> None:
        browser, playwright = self._browser, self._playwright
        self._browser = self._playwright = None
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                log.debug("browser did not close cleanly", exc_info=True)
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                log.debug("playwright did not stop cleanly", exc_info=True)


def _watch(page: Page, errors: list[str], failed: list[str]) -> None:
    """Keep what the page complained about. A blank screenshot with a console error under
    it is a diagnosis; a blank screenshot alone is a mystery."""

    def on_console(message: object) -> None:
        kind = as_str(getattr(message, "type", ""))
        if kind == "error":
            errors.append(as_str(getattr(message, "text", ""))[:200])

    def on_page_error(error: object) -> None:
        errors.append(str(error).splitlines()[0][:200] if str(error) else "page error")

    def on_request_failed(request: object) -> None:
        failure = getattr(request, "failure", None)
        why = as_str(failure) if isinstance(failure, str) else "failed"
        failed.append(f"{as_str(getattr(request, 'url', ''))[:160]} ({why})")

    def on_response(response: object) -> None:
        status = as_int(getattr(response, "status", 0))
        if status >= 400:
            failed.append(f"{as_str(getattr(response, 'url', ''))[:160]} ({status})")

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)


def reading_of(shot: Capture, written: Path) -> str:
    """The capture as the model sees it: where the file went, and what the page is."""
    width, height = shot.viewport
    doc_w, doc_h = shot.document
    lines = [
        f"screenshot of {shot.url} written to {written} "
        + f"({width}x{height} viewport, {len(shot.png) // 1024} kB png)",
        f"title: {shot.title or '(none)'}",
    ]
    wide = f"; WIDER THAN THE VIEWPORT by {doc_w - width}px, so it scrolls sideways"
    lines.append(f"document: {doc_w}x{doc_h}{wide if doc_w > width else ''}")
    if shot.headings:
        lines.append("headings: " + " | ".join(shot.headings))
    else:
        lines.append("headings: none")
    if shot.landmarks:
        lines.append(f"landmarks: {shot.landmarks}")
    alt = f" ({shot.images_without_alt} without alt)" if shot.images_without_alt else ""
    lines.append(
        f"links: {shot.links}, images: {shot.images}{alt}, text: {shot.text_chars} chars"
    )
    lines.append(
        f"body: font {shot.font or '?'} {shot.font_size}, color {shot.color or '?'}, "
        + f"background {shot.background or '?'}"
    )
    lines.append(
        "console errors: "
        + ("; ".join(shot.console_errors) if shot.console_errors else "none")
    )
    lines.append(
        "failed requests: "
        + ("; ".join(shot.failed_requests) if shot.failed_requests else "none")
    )
    return "\n".join(lines)


def save_png(png: bytes, folder: Path, stem: str) -> Path:
    """Write the image where the harness keeps its own files, under a name that sorts."""
    from datetime import UTC, datetime

    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in stem)[:40].strip("-")
    path = folder / f"{stamp}{'-' + safe if safe else ''}.png"
    _ = path.write_bytes(png)
    return path

