"""Rendering a page the way a browser would, for the pages that need it.

`open_url` fetches HTML and reads it. Most pages are readable that way; some ship an empty
shell and build everything in JavaScript, and for those the fetch reads as nothing. This
is the fallback for that case, and only that case: a headless Chromium, driven through
Playwright, that loads the page, waits for it to settle, and hands back the DOM as HTML
for the same reader-mode extractor to read.

Three rules, because a browser is a much larger thing than an HTTP client:

  * **Every request the page makes is checked, not only the one the model typed.** A page
    can load a subresource from, or redirect to, an address on this machine or its
    network, and a browser that followed it would undo the guard the fetch path keeps.
    Playwright's request interception runs `address_error` on each one and refuses the
    ones it refuses.
  * **It is bounded.** Images, fonts and media are not loaded, downloads are refused,
    there is no profile, one browser serves the whole process and starts only when first
    needed, and a page gets `render_timeout` seconds to settle before it is read as it is.
  * **It is optional.** Playwright and its Chromium are an extra, and a harness without
    them says so in the tool result, with the two commands that install them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from harness.settings import Web as WebSettings

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

log = logging.getLogger(__name__)

INSTALL = "uv sync --extra browser && uv run harness install-browser"

#: Resource kinds a render does not need. Skipping them is most of the speed.
SKIPPED = frozenset({"image", "media", "font", "stylesheet", "manifest", "texttrack"})


class RenderUnavailable(Exception):
    """There is no browser to render with. The message says how to get one."""


class RenderFailed(Exception):
    """The browser could not load the page. The message says why."""


class Renderer(Protocol):
    """What `open_url` falls back to. One method, and the way to hang up."""

    async def render(self, url: str) -> str:
        """The page's HTML after it has run, or raise `RenderUnavailable` / `RenderFailed`."""
        ...

    async def aclose(self) -> None: ...


def new_renderer(settings: WebSettings | None = None) -> Renderer:
    return _Chromium(settings or WebSettings())


@dataclass
class _Chromium:
    settings: WebSettings
    _playwright: Playwright | None = field(default=None, repr=False)
    _browser: Browser | None = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def render(self, url: str) -> str:
        browser = await self._launched()
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import Route

        from harness.tools.addresses import USER_AGENT, address_error

        context = await browser.new_context(
            user_agent=USER_AGENT,
            java_script_enabled=True,
            accept_downloads=False,
            viewport={"width": 1280, "height": 900},
        )
        try:
            page = await context.new_page()

            async def guard(route: Route) -> None:
                request = route.request
                if request.resource_type in SKIPPED:
                    await route.abort()
                    return
                refusal = await address_error(request.url, self.settings.block_private)
                if refusal:
                    log.info("render of %s refused a request: %s", url, refusal)
                    await route.abort()
                    return
                await route.continue_()

            _ = await page.route("**/*", guard)
            timeout = int(self.settings.render_timeout * 1000)
            try:
                _ = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                # Settled, or as settled as it gets in the time: a page that polls forever
                # never reaches "networkidle", and the DOM it has by then is the answer.
                with contextlib.suppress(PlaywrightError):
                    await page.wait_for_load_state("networkidle", timeout=timeout)
                return await page.content()
            except PlaywrightError as exc:
                raise RenderFailed(f"the browser could not load {url}: {exc}") from exc
        finally:
            await context.close()

    async def _launched(self) -> Browser:
        async with self._lock:
            if self._browser is not None:
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RenderUnavailable(
                    "the page builds itself with JavaScript and no browser is installed "
                    + f"to run it. Install one with: {INSTALL}"
                ) from exc
            try:
                playwright = await async_playwright().start()
                browser = await playwright.chromium.launch(headless=True)
            except Exception as exc:
                raise RenderUnavailable(
                    "the page builds itself with JavaScript and the browser could not "
                    + f"start ({str(exc).splitlines()[0][:160]}). Install it with: {INSTALL}"
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
