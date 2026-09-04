"""A page as Safari's engine has it, through `wkrender`.

`wkrender` is a small Swift command in its own repository, beside this one: it loads a
URL in a headless `WKWebView` presenting as the Safari installed on this Mac, waits for
the DOM to settle, and prints what it found -- the HTML, the value of a script run in
the page, a PNG of it, what the page wrote to the console and which of its resources
failed to load. It exists because of what was measured on 2026-09-03: DuckDuckGo answers
a fetch carrying a browser's headers with a challenge, and served the headless Chromium
this harness used to render with an error page on every surface -- and a real WebKit,
with nothing special done, gets the results. This is the renderer talkie's web search
uses, and the reason that search is rarely rate-limited. It is now the one browser the
harness has: `web_search`, `open_url`'s render and `screenshot` all go through it.

Reached as a subprocess, one per render, the way the language servers are reached: the
binary lives in the harness's own folder (`~/.harness/bin/wkrender`), put there by
`harness install-webkit`, and `PATH` is not consulted, because a binary found there is
one nobody chose. Without it the caller is told the one command that installs it.

macOS only, by nature. On another platform the answer is the same sentence.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from harness.types import as_dict, as_list, as_str

#: Beside `servers/bin`, for the same reason: one place, chosen on purpose.
BIN = Path("~/.harness/bin")
BINARY = "wkrender"
INSTALL = "uv run harness install-webkit"


class RenderUnavailable(Exception):
    """There is no browser to render with. The message says how to get one."""


class RenderFailed(Exception):
    """The browser could not load the page. The message says why."""


@dataclass(frozen=True, slots=True)
class Rendered:
    """What `wkrender` printed."""

    url: str
    title: str
    html: str
    #: The value of `--eval`, as JSON gave it back, or `None`.
    eval: object = None
    #: What the page wrote to `console.error` or threw.
    errors: tuple[str, ...] = ()
    #: Subresources whose load failed, as `URL (tag failed to load)`.
    failed: tuple[str, ...] = ()


@dataclass
class WebKit:
    """The `wkrender` command, if it is installed.

    `path` is an override, from `[web] webkit`; otherwise the harness's own bin folder.
    Checked at each call rather than once, so installing the binary mid-run is enough.
    """

    path: str = ""
    #: Seconds a render may take, including a challenge clearing. Sized like talkie's:
    #: a JavaScript interstitial and its reload need most of this.
    timeout: float = 20.0

    @property
    def binary(self) -> Path | None:
        if self.path:
            chosen = Path(self.path).expanduser()
            return chosen if chosen.is_file() else None
        chosen = (BIN / BINARY).expanduser()
        return chosen if chosen.is_file() else None

    @property
    def available(self) -> bool:
        return self.binary is not None

    async def render(
        self,
        url: str,
        *,
        width: int = 1280,
        height: int = 900,
        dark: bool = False,
        script: str = "",
        png: Path | None = None,
        full_page: bool = False,
        files_under: Path | None = None,
        block_private: bool = False,
    ) -> Rendered:
        """The page, or raise `RenderUnavailable` (no binary) or `RenderFailed`.

        `script` runs in the settled page and its value comes back as `eval`; `png` is
        where a picture of the viewport (or, with `full_page`, the document) is written;
        `files_under` is the one folder a `file:` page may load from; `block_private`
        refuses anything on this machine or its private network.
        """
        binary = self.binary
        if binary is None:
            raise RenderUnavailable(
                f"{BINARY} is not installed, so a page cannot be read through Safari's "
                + f"engine. Install it with: {INSTALL}"
            )
        argv = [str(binary), "--json", "--timeout", str(int(self.timeout))]
        argv += ["--viewport", f"{width}x{height}"]
        if dark:
            argv.append("--dark")
        if script:
            argv += ["--eval", script]
        if png is not None:
            argv += ["--png", str(png)]
            if full_page:
                argv.append("--full-page")
        if files_under is not None:
            argv += ["--files-under", str(files_under)]
        if block_private:
            argv.append("--block-private")
        argv.append(url)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RenderFailed(f"{BINARY} could not start: {exc}") from exc
        try:
            async with asyncio.timeout(self.timeout + 10):
                out, err = await process.communicate()
        except TimeoutError as exc:
            process.kill()
            _ = await process.wait()
            raise RenderFailed(
                f"{BINARY} did not answer within {self.timeout + 10:.0f}s"
            ) from exc
        if process.returncode != 0:
            said = err.decode("utf-8", errors="replace").strip().splitlines()
            why = said[-1] if said else f"exit {process.returncode}"
            raise RenderFailed(f"{BINARY} could not load {url}: {why}")
        try:
            row = as_dict(cast("object", json.loads(out.decode("utf-8", errors="replace"))))
        except json.JSONDecodeError as exc:
            raise RenderFailed(f"{BINARY} printed something that is not JSON") from exc
        return Rendered(
            url=as_str(row.get("url")) or url,
            title=as_str(row.get("title")),
            html=as_str(row.get("html")),
            eval=row.get("eval"),
            errors=tuple(as_str(e) for e in as_list(row.get("errors"))),
            failed=tuple(as_str(f) for f in as_list(row.get("failed"))),
        )


def adopt(source: Path | None = None) -> Path:
    """Put `wkrender` in the harness's bin folder, from a built checkout or from `PATH`.

    `source` is a file or the folder of a built checkout (`.build/release/wkrender`
    inside it). Raises `FileNotFoundError` with a sentence when there is nothing to
    adopt.
    """
    target = (BIN / BINARY).expanduser()
    candidates: list[Path] = []
    if source is not None:
        source = source.expanduser()
        candidates += [source, source / ".build" / "release" / BINARY]
    if (found := shutil.which(BINARY)) is not None:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            # The installed binary itself, offered back -- `--from ~/.harness/bin`, or
            # `PATH` holding that folder. Unlinking the target first deleted it.
            if target.exists() and candidate.resolve() == target.resolve():
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            _ = shutil.copy2(candidate, target)
            target.chmod(0o755)
            return target
    raise FileNotFoundError(
        f"no {BINARY} to install: build it first (swift build -c release in its checkout)"
    )
