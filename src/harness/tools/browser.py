"""Rendering a page the way a browser would, for the pages that need it.

`open_url` fetches HTML and reads it. Most pages are readable that way; some ship an empty
shell and build everything in JavaScript, and some answer a fetch with a bot check. For
those the page is rendered in a browser and read the same way. `screenshot` uses the same
browser to look at a page at a viewport, and comes back with a PNG and a reading.

The browser is Safari's engine, through `wkrender` (`tools/webkit.py`). It was a headless
Chromium driven by Playwright until 2026-09-03, when it was measured that the sites
which challenge a fetch also refuse that Chromium as automation, and that a WKWebView
presenting as the installed Safari is served the page. One engine for search, reading
and screenshots, and one optional dependency fewer.

Three rules, because a browser is a much larger thing than an HTTP client:

  * **Every navigation is checked, not only the one the model typed.** A page can
    redirect to, or frame, an address on this machine or its network; `wkrender`
    refuses those when told to, resolving the host, and blocks subresources at a
    literal private address by rule. A subresource named by a hostname that resolves to
    a private address is the one gap, stated in `wkrender`'s own notes.
  * **It is bounded.** A capture of a file in the working folder may load files under
    that folder and nothing else on disk; downloads and popups do not happen; there is no
    profile; and each attempt gets at most `render_timeout` seconds, including startup.
  * **It is optional.** A harness without the binary says so in the tool result, with the
    command that installs it.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from harness.settings import Web as WebSettings
from harness.tools.webkit import (
    INSTALL,
    Rendered,
    RenderFailed,
    RenderUnavailable,
    WebKit,
)
from harness.types import as_dict, as_int, as_list, as_str

__all__ = [
    "INSTALL",
    "Capture",
    "RenderFailed",
    "RenderUnavailable",
    "Renderer",
    "new_renderer",
    "reading_of",
    "save_png",
]

#: How many console errors and failed requests a capture keeps. Enough to see a pattern;
#: a page that logs in a loop would otherwise return the loop.
KEPT = 8


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

    async def render(self, url: str) -> Rendered:
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
    settings = settings or WebSettings()
    return _Safari(settings, WebKit(path=settings.webkit, timeout=settings.render_timeout))


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
class _Safari:
    """The `Renderer` over `wkrender`. Stateless: one process per render."""

    settings: WebSettings
    webkit: WebKit = field(default_factory=WebKit)

    async def render(self, url: str) -> Rendered:
        page = await self.webkit.render(
            url, reader=True, block_private=self.settings.block_private
        )
        return page

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
        # The PNG goes through a file because that is how `wkrender` hands it over; the
        # caller decides where it lives for good, so this one is temporary.
        with tempfile.TemporaryDirectory(prefix="wkrender-") as folder:
            shot = Path(folder) / "shot.png"
            page = await self.webkit.render(
                url,
                width=width,
                height=height,
                dark=dark,
                script=_READING,
                png=shot,
                full_page=full_page,
                files_under=files_under,
                block_private=self.settings.block_private,
            )
            png = await asyncio.to_thread(shot.read_bytes) if shot.exists() else b""
        reading = as_dict(page.eval)
        document = [as_int(n) for n in as_list(reading.get("document"))]
        return Capture(
            png=png,
            url=page.url,
            title=page.title,
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
            console_errors=page.errors[:KEPT],
            failed_requests=page.failed[:KEPT],
        )

    async def aclose(self) -> None:
        return None


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
        "console errors: " + ("; ".join(shot.console_errors) if shot.console_errors else "none")
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
