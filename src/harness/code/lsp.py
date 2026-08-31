"""One language server, over LSP on a pipe.

Everything LSP-shaped lives in this file: JSON-RPC framing, the handshake, URIs, and the
0-based columns the wire uses. `Symbol`, `Location` and the tools see none of it, for the
same reason `Message` knows nothing about OpenAI's wire format.

**This is shared, and that is a measurement rather than a guess.** It was written for
basedpyright, and then Go arrived: what differed was a command, two extensions and a
language id, so `gopls.py` is fifteen lines. Two implementations is the point at which
sharing is a fact. A backend that does not speak LSP at all -- an in-process library, say --
satisfies `CodeIndex` directly and ignores this file, which is why the protocol is defined
next door and not here.

**The framing is written out rather than imported.** LSP over stdio is a `Content-Length`
header, a blank line and a JSON body -- forty lines, once. `pygls` is the obvious library
and it does not help: it is for *writing* language servers, and its docs list clients as
"Coming Soon". A dependency that does not cover the case is worse than none, and
`pyproject.toml` makes every dependency argue for itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.code.base import CodeIndexError, Location, Symbol
from harness.code.servers import servers_bin
from harness.settings import Code

log = logging.getLogger(__name__)

#: LSP numbers its symbol kinds. Only the ones a person would say out loud are named; the
#: rest arrive as "symbol", which is honest and costs a model nothing.
KINDS = {
    5: "class",
    6: "method",
    12: "function",
    13: "variable",
    14: "constant",
    11: "interface",
    23: "struct",
    10: "enum",
    2: "module",
}


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def _path(uri: str) -> Path:
    from urllib.parse import unquote, urlparse

    return Path(unquote(urlparse(uri).path))


@dataclass(frozen=True, slots=True)
class Recipe:
    """How to obtain one server, and what to say when it cannot be obtained.

    On the language's own file, beside its command and extensions, because adding a
    language must stay one file -- an install recipe kept in a central table is a second
    place to edit and a second place to forget.

    `install` is argv, run only by `harness --install-servers`, never during a run. Fetching
    272MB inside a tool call would blow the request timeout, and failing halfway is strictly
    worse than "not installed, use grep", which already degrades correctly.

    `doc` is for the servers that cannot be installed for you -- `gopls` is `go install`
    only, with no official prebuilt binary, so a machine without a Go toolchain gets a
    sentence a person can act on instead of a failed download.
    """

    #: The executable to look for and to link as. Also the name in the bin folder.
    binary: str
    #: Argv that provisions it, or empty when only a person can.
    install: tuple[str, ...] = ()
    #: What to tell someone who has to do it themselves.
    doc: str = ""


@dataclass
class LspIndex:
    """One language server process, over one folder.

    Started on the first question rather than at construction, so a run that never asks
    about code never pays for an index -- and a missing binary is discovered by the tool
    that wanted it, which is the only place that can say anything useful about it.

    A language is three facts: what it is called, what it runs, and which files it speaks
    for. Everything else below is the same for every server that speaks LSP.
    """

    root: Path
    settings: Code = field(default_factory=Code)

    name: str = "lsp"
    #: Arguments after the binary. The binary itself comes from the harness's own bin
    #: folder -- see `argv`.
    arguments: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    language_id: str = ""
    recipe: Recipe = field(default_factory=lambda: Recipe(binary=""))

    @property
    def argv(self) -> tuple[str, ...]:
        """What to run: an override, or the binary the harness manages.

        One lookup location, deliberately. `PATH` is not consulted, because a server found
        there is one nobody chose: it may be any version, and on a server started at boot it
        may be absent entirely while a developer shell has it. `--install-servers` links
        what is on `PATH` into the harness's folder, which turns "happens to be installed"
        into "was adopted on purpose" -- and makes the missing case one sentence a person
        can act on rather than a guess about their environment.
        """
        override = self.settings.commands.get(self.name)
        if override:
            return tuple(override)
        return (str(servers_bin() / self.recipe.binary), *self.arguments)

    _process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    _reader: asyncio.Task[None] | None = field(default=None, repr=False)
    _pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict, repr=False)
    _opened: set[Path] = field(default_factory=set, repr=False)
    _next_id: int = field(default=0, repr=False)
    _starting: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    #: Whether anything has ever answered. Until it has, an empty result may only mean the
    #: index is still being built.
    _warm: bool = field(default=False, repr=False)

    # -- lifecycle -------------------------------------------------------------------

    async def _ensure(self) -> None:
        """Start and hand-shake, once. Idempotent, and safe under concurrent callers."""
        async with self._starting:
            if self._process is not None and self._process.returncode is None:
                return
            self._pending.clear()
            self._opened.clear()
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *self.argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=self.root,
                    env={**os.environ},
                )
            except (FileNotFoundError, PermissionError) as exc:
                # `available=False`: this will not become installed during the run, so the
                # caller must stop asking rather than spend the budget re-discovering it.
                raise CodeIndexError(
                    f"{self.recipe.binary} is not set up for the harness. "
                    f"Run `harness --install-servers` to provision it, "
                    f"or use grep and read_file instead.",
                    available=False,
                ) from exc

            self._reader = asyncio.create_task(self._read())
            try:
                await asyncio.wait_for(
                    self._request(
                        "initialize",
                        {
                            "processId": os.getpid(),
                            "rootUri": _uri(self.root),
                            "workspaceFolders": [
                                {"uri": _uri(self.root), "name": self.root.name}
                            ],
                            "capabilities": {
                                "workspace": {"symbol": {"dynamicRegistration": False}},
                                "textDocument": {
                                    "definition": {"linkSupport": False},
                                    "references": {"dynamicRegistration": False},
                                },
                            },
                        },
                    ),
                    timeout=self.settings.startup_timeout,
                )
            except TimeoutError as exc:
                await self.aclose()
                raise CodeIndexError(
                    f"{self.name} did not finish starting in "
                    f"{self.settings.startup_timeout:.0f}s"
                ) from exc
            self._notify("initialized", {})
            self._warm = False

    async def aclose(self) -> None:
        """Stop the process. Safe twice, and after it has already died."""
        process, self._process = self._process, None
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel()
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5)
            except (ProcessLookupError, TimeoutError):
                with suppress(ProcessLookupError):
                    process.kill()
        for waiting in self._pending.values():
            if not waiting.done():
                waiting.cancel()
        self._pending.clear()

    # -- the wire --------------------------------------------------------------------

    async def _read(self) -> None:
        """One frame at a time, forever, resolving whoever asked.

        On EOF every pending request is failed rather than left hanging. A language server
        that dies mid-question is the failure that is invisible from outside: the future
        never settles, the tool waits out its timeout, and the run looks slow rather than
        broken.
        """
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = await stream.readline()
                    if not line:
                        raise EOFError
                    if line in (b"\r\n", b"\n"):
                        break
                    key, _, value = line.decode("utf-8", "replace").partition(":")
                    headers[key.strip().lower()] = value.strip()
                size = int(headers.get("content-length", 0))
                body = await stream.readexactly(size) if size else b"{}"
                self._settle(json.loads(body))
        except (EOFError, asyncio.IncompleteReadError, json.JSONDecodeError):
            self._fail_all(f"{self.name} stopped responding")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a defect here must not leave callers hanging
            log.exception("%s reader failed", self.name)
            self._fail_all(f"{self.name} reader failed: {exc}")

    def _settle(self, message: dict[str, Any]) -> None:
        waiting = self._pending.pop(message.get("id", -1), None)
        if waiting is None or waiting.done():
            return  # a notification, a server request, or a reply nobody is waiting for
        if "error" in message:
            waiting.set_exception(
                CodeIndexError(f"{self.name}: {message['error'].get('message', 'error')}")
            )
        else:
            waiting.set_result(message.get("result"))

    def _fail_all(self, reason: str) -> None:
        for waiting in self._pending.values():
            if not waiting.done():
                waiting.set_exception(CodeIndexError(reason))
        self._pending.clear()

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise CodeIndexError(f"{self.name} is not running")
        body = json.dumps({"jsonrpc": "2.0", **payload}).encode()
        process.stdin.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))

    def _request(self, method: str, params: dict[str, Any]) -> asyncio.Future[Any]:
        self._next_id += 1
        waiting: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = waiting
        self._send({"id": self._next_id, "method": method, "params": params})
        return waiting

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    async def _ask(self, method: str, params: dict[str, Any]) -> Any:
        await self._ensure()
        try:
            return await asyncio.wait_for(
                self._request(method, params), timeout=self.settings.request_timeout
            )
        except TimeoutError as exc:
            raise CodeIndexError(
                f"{self.name} did not answer {method} in "
                f"{self.settings.request_timeout:.0f}s"
            ) from exc

    async def _indexed(self, method: str, params: dict[str, Any]) -> Any:
        """Ask, and keep asking while the index may still be cold.

        Only ever slow once. The retry exists because "nothing found" and "nothing indexed
        yet" are the same reply, and answering "no definitions" for a symbol that is simply
        not loaded yet is a wrong answer a model cannot check. Once any query returns
        something the index is warm, and from then on an empty result is believed
        immediately -- so a run pays this at most once, and only if its first question
        happens to be about a symbol that is not there.
        """
        deadline = asyncio.get_running_loop().time() + self.settings.warmup
        while True:
            found = await self._ask(method, params)
            if found or self._warm:
                self._warm = self._warm or bool(found)
                return found
            if asyncio.get_running_loop().time() >= deadline:
                return found
            await asyncio.sleep(0.25)

    def _open(self, path: Path) -> None:
        """Tell the server about a file before asking about a position in it.

        Every server answers positional questions about open documents; not all answer
        about files they have merely indexed. Opening is cheap, idempotent here, and
        removes a difference between backends that would otherwise show up as an empty
        result nobody can explain.
        """
        if path in self._opened:
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise CodeIndexError(f"cannot read {path}: {exc}") from exc
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": _uri(path),
                    "languageId": self.language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )
        self._opened.add(path)

    # -- the contract ----------------------------------------------------------------

    async def definitions(self, name: str, *, near: Path | None = None) -> tuple[Symbol, ...]:
        container, _, bare = name.rpartition(".")
        found = await self._indexed("workspace/symbol", {"query": bare})
        symbols: list[Symbol] = []
        for entry in found or []:
            # Servers disagree about where qualification lives, and both are LSP-legal.
            # basedpyright: name="build", containerName="Widget". gopls:
            # name="Widget.Build", containerName="shop" -- the *package*. So the last
            # dotted segment of `name` is the symbol, and a prefix on it outranks
            # `containerName`, which may be describing something else entirely. Matching
            # `name` exactly finds every Python method and no Go one.
            qualifier, _, offered = (entry.get("name") or "").rpartition(".")
            if offered != bare:
                continue  # the server matches loosely; answer about the name asked
            location = entry.get("location") or {}
            uri = location.get("uri") or (entry.get("location") or {}).get("targetUri")
            if not uri:
                continue
            path = _path(uri)
            if near is not None and path != near.resolve():
                continue
            found_container = qualifier or entry.get("containerName") or ""
            if container and container.split(".")[-1] != found_container.split(".")[-1]:
                continue
            line = (location.get("range") or {}).get("start", {}).get("line", 0) + 1
            symbols.append(
                Symbol(
                    name=offered,
                    location=Location(path, line, _line_text(path, line)),
                    kind=KINDS.get(entry.get("kind", 0), "symbol"),
                    container=found_container,
                )
            )
        return tuple(symbols)

    async def references(self, symbol: Symbol) -> tuple[Location, ...]:
        path = symbol.location.path
        # Before starting anything. A wrong line is answerable from the file alone, and
        # spinning up a language server -- 272MB of Node, several seconds cold -- to
        # discover it is work nobody needed.
        column = _column_of(path, symbol.location.line, symbol.name)
        if column is None:
            raise CodeIndexError(
                f"{symbol.name!r} does not appear on {path.name}:{symbol.location.line}, "
                f"which reads: {_line_text(path, symbol.location.line).strip()[:80]!r}. "
                "Check the line, or call find_definition again if the file has changed."
            )

        await self._ensure()
        self._open(path)
        found = await self._ask(
            "textDocument/references",
            {
                "textDocument": {"uri": _uri(path)},
                "position": {"line": symbol.location.line - 1, "character": column},
                "context": {"includeDeclaration": True},
            },
        )
        places: list[Location] = []
        for entry in found or []:
            where = _path(entry["uri"])
            line = entry["range"]["start"]["line"] + 1
            places.append(Location(where, line, _line_text(where, line)))
        return tuple(places)


def _line_text(path: Path, line: int) -> str:
    """The line itself, so a hit answers the question instead of prompting a read."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for number, text in enumerate(handle, 1):
                if number == line:
                    return text.rstrip("\n")
    except OSError:
        return ""
    return ""


def _column_of(path: Path, line: int, name: str) -> int | None:
    """Where `name` sits on that line, 0-based for the wire.

    Found here rather than carried through the model's arguments. A column a model counted
    is a column that can be wrong by one, and a position off by one returns a confident
    answer about the wrong symbol -- a failure with no symptom at all.
    """
    text = _line_text(path, line)
    index = text.find(name)
    return index if index >= 0 else None
