"""A picture of a page, and a reading of it.

`screenshot` loads a page in the same headless Safari engine `open_url` renders with, at
a viewport the model chooses, and writes what it saw as a PNG under `~/.harness/screenshots/`.
The result the model reads is not the picture. It is what the browser can say about the
page in a few lines: the title, whether the document is wider than the viewport, the
headings and landmarks, the body's font and colours, every console error, every request
that failed. A text-only model can act on all of that -- a page that scrolls sideways at
390 pixels wide is a layout bug whether or not anyone can see it -- and a person opens
the file.

**Why the model does not see the image.** `Message.content` is a string, and so is every
transcript row, every provider request, every compaction note and every editor update
built on it. Carrying an image would change the type the whole harness is made of, and
the ladder's discipline is that a rung runs the artifact rather than reading the answer,
so a model judging its own screenshot would be the verification `loop.py` refuses. The
reading is the part that is checkable. `evals/DESIGN.md` has the rest of that argument,
and `docs/adr/0023` the decision; the day a measurement asks for the picture, the PNG is
already on disk and the tool result already names it.

**A file in the working folder is a page too.** `index.html` written a moment ago is the
thing most worth looking at, so the tool takes a workspace path as well as a URL, turns it
into `file://`, and lets the page load files from the working folder and nowhere else on
the disk. A URL is checked against the same address rules as `open_url`.

Not asked about, like `open_url`: it reads a page and writes a file into the harness's own
folder, and neither changes the machine a person would want a prompt for.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from harness.settings import Web as WebSettings
from harness.tools.addresses import address_error
from harness.tools.base import Arguments, Handler, Minimum, ToolContext, bind, spec_for
from harness.tools.browser import (
    INSTALL,
    Capture,
    Renderer,
    RenderFailed,
    RenderUnavailable,
    reading_of,
    save_png,
)
from harness.types import ToolResult, ToolSpec
from harness.workspace import WorkspaceError

#: Beside `threads/`, `processes/` and `servers/`. Images from a tool server land here too.
SCREENSHOTS = Path("~/.harness/screenshots")

#: The ceiling `wkrender` accepts for a viewport.
WIDEST, TALLEST = 3840, 2160


@dataclass(frozen=True, slots=True)
class Shot(Arguments):
    url: Annotated[
        str,
        "An http(s) URL, or the path of an HTML file in the working folder, like "
        + "index.html or site/about.html.",
    ]
    width: Annotated[
        int, "Viewport width in pixels. 1280 is a laptop, 390 a phone.", Minimum(200)
    ] = 1280
    height: Annotated[int, "Viewport height in pixels.", Minimum(200)] = 900
    full_page: Annotated[
        bool, "Capture the whole scrollable page rather than the first viewport of it."
    ] = False
    dark: Annotated[bool, "Render as if the person prefers a dark colour scheme."] = False


@dataclass
class Screenshot:
    """`screenshot`. The page at a viewport, as a file for a person and a reading for the
    model."""

    settings: WebSettings = field(default_factory=WebSettings)
    renderer: Renderer | None = None
    folder: Path = SCREENSHOTS
    spec: ToolSpec = field(
        default=spec_for(
            Shot,
            name="screenshot",
            description=(
                "Load a page in a headless browser at a chosen viewport and save a PNG of "
                + "it for the user to look at. You do not see the picture; you get what "
                + "the browser can say about the page: the title, the document size "
                + "against the viewport (wider means it scrolls sideways), the headings "
                + "and landmarks, the body font and colours, console errors, and requests "
                + "that failed. Use it to check a page you are building -- give the path "
                + "of the HTML file in the working folder, or a URL -- at a phone width "
                + "and a laptop width, in light and dark, and act on what comes back. "
                + "Stylesheets, images and fonts are loaded, so a missing file shows up "
                + "as a failed request."
            ),
        )
    )

    def preview(self, args: Shot, /) -> tuple[str, str]:
        return f"Screenshot {args.url} at {args.width}x{args.height}", "screenshot"

    async def run(self, args: Shot, ctx: ToolContext, /) -> ToolResult:
        target = args.url.strip()
        if not target:
            return ToolResult(
                "say which page: a URL or a file in the folder", ok=False, refused=True
            )
        if args.width > WIDEST or args.height > TALLEST:
            return ToolResult(
                f"the viewport may be at most {WIDEST}x{TALLEST}", ok=False, refused=True
            )

        files_under: Path | None = None
        if urlsplit(target).scheme in ("http", "https"):
            refusal = await address_error(target, self.settings.block_private)
            if refusal:
                return ToolResult(refusal, ok=False, refused=True)
        elif urlsplit(target).scheme:
            return ToolResult(
                "only http(s) URLs and files in the folder can be captured, not "
                + f"{urlsplit(target).scheme!r}",
                ok=False,
                refused=True,
            )
        else:
            try:
                path = ctx.paths.resolve(target)
            except WorkspaceError as exc:
                return ToolResult(str(exc), ok=False, refused=True)
            if not path.is_file():
                return ToolResult(f"{target} is not a file in the folder", ok=False)
            files_under = next(r for r in ctx.paths.roots if path.is_relative_to(r))
            target = path.as_uri()

        if self.renderer is None or not self.settings.render:
            return ToolResult(
                f"no browser is available here to capture {target}. Install it with: "
                + INSTALL,
                ok=False,
            )
        try:
            shot: Capture = await self.renderer.capture(
                target,
                width=args.width,
                height=args.height,
                full_page=args.full_page,
                dark=args.dark,
                files_under=files_under,
            )
        except RenderUnavailable as exc:
            return ToolResult(f"cannot capture {target}: {exc}", ok=False)
        except RenderFailed as exc:
            return ToolResult(str(exc), ok=False)

        stem = _stem(shot.url, args.width, args.dark)
        written = await asyncio.to_thread(save_png, shot.png, self.folder.expanduser(), stem)
        return ToolResult(reading_of(shot, written))


def _stem(url: str, width: int, dark: bool) -> str:
    """`index-html-390-dark`: a name a person can tell apart in a folder of them."""
    parts = urlsplit(url)
    name = Path(parts.path).name or parts.netloc or "page"
    return f"{name}-{width}{'-dark' if dark else ''}"


def screenshot_tools(
    settings: WebSettings | None = None,
    renderer: Renderer | None = None,
    folder: Path = SCREENSHOTS,
) -> list[Handler]:
    return [bind(Screenshot(settings or WebSettings(), renderer, folder))]
