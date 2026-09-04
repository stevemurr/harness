"""A page as Safari's engine has it, through `wkrender`.

`wkrender` is a small Swift command in its own repository, beside this one: it loads a
URL in a headless `WKWebView` presenting as the Safari installed on this Mac, waits for
the DOM to settle, and prints the HTML. It exists because of what was measured on
2026-09-03: DuckDuckGo answers a fetch carrying a browser's headers with a challenge, and
serves the headless Chromium `open_url` renders with an error page on every surface --
and a real WebKit, with nothing special done, gets the results. This is the renderer
talkie's web search uses, and the reason that search is rarely rate-limited.

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

from harness.tools.browser import RenderFailed, RenderUnavailable
from harness.types import as_dict, as_str

#: Beside `servers/bin`, for the same reason: one place, chosen on purpose.
BIN = Path("~/.harness/bin")
BINARY = "wkrender"
INSTALL = "uv run harness install-webkit"


@dataclass(frozen=True, slots=True)
class Rendered:
    """What `wkrender` printed: where the page ended up, its title, and its HTML."""

    url: str
    title: str
    html: str


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

    async def render(self, url: str) -> Rendered:
        """The page, or raise `RenderUnavailable` (no binary) or `RenderFailed`."""
        binary = self.binary
        if binary is None:
            raise RenderUnavailable(
                f"{BINARY} is not installed, so a page cannot be read through Safari's "
                + f"engine. Install it with: {INSTALL}"
            )
        try:
            process = await asyncio.create_subprocess_exec(
                str(binary),
                "--json",
                "--timeout",
                str(int(self.timeout)),
                url,
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
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            _ = shutil.copy2(candidate, target)
            target.chmod(0o755)
            return target
    raise FileNotFoundError(
        f"no {BINARY} to install: build it first (swift build -c release in its checkout)"
    )
