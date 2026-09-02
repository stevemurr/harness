"""The code-navigation boundary.

One protocol, one file per language. Adding a language is implementing this and registering
it; nothing else in the harness learns the language exists -- not the loop, not the tools,
not the approval layer.

**Not called a language server**, because the first thing it is not is a transport. LSP is
how `pyright.py` happens to answer, the way HTTP is how `providers/openai.py` happens to
answer, and an in-process backend must be able to satisfy this without pretending to be a
server. Naming a protocol after one implementation's transport is how the first
implementation written becomes the one every other has to imitate -- the mistake
`providers/base.py` avoids by keeping `to_openai()` off `Message`.

**Resolution is two steps, and the types make it unskippable.**

    definitions("run")          -> every symbol that name could mean
    references(symbol)          -> where that ONE symbol is used

`references` takes a `Symbol`, never a string, because a bare name does not denote one
thing: `run` is sixteen different methods in this repository. A one-step
`references("run")` has to either pick one silently or return the union, and both are
answers that look right and are wrong. The tool surface enforces the same rule with the
machinery that already exists -- `path` and `line` are `required` in the schema, so
`Registry.run` refuses a one-step call before the tool is reached.

The step is not busywork for a model that already knows where the symbol is: a model that
has just read the file has the path and line in hand and goes straight to step two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Location:
    """Where something is.

    `line` is **1-based**, matching `read_file`, `edit_file` and how a person reads an
    error. Any 0-based wire convention is converted inside the implementation that speaks
    it, beside the URI encoding, because an off-by-one that leaks up here becomes an
    off-by-one in every language added afterwards.

    `text` is the line itself. Without it every hit costs a follow-up `read_file`, which is
    the difference between a tool that answers a question and one that starts a
    conversation. `grep` returns the line; so does this.
    """

    path: Path
    line: int
    text: str = ""


@dataclass(frozen=True, slots=True)
class Symbol:
    """One thing a name refers to, and the token `references` takes.

    It exists so that step two cannot be called with a name. `container` and `kind` are for
    a person and a model choosing between candidates -- `AgentLoop.run` beside `Shell.run`
    is a choice someone can make, `run` beside `run` is not.
    """

    name: str
    location: Location
    kind: str = ""
    container: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.container}.{self.name}" if self.container else self.name


class CodeIndexError(Exception):
    """The index could not answer.

    `available` is the distinction worth getting right in both directions, the way
    `ProviderError.retryable` is. A backend that is not installed will not become installed
    during this run, so retrying it on every call spends the budget discovering the same
    thing; a backend that crashed once may well answer the next question. Getting it
    backwards is a run that either gives up on a working tool or hammers a missing one.
    """

    def __init__(self, message: str, *, available: bool = True) -> None:
        super().__init__(message)
        self.available: bool = available


#: Beside `threads/` and `config.toml`, for the same reason: one folder a person can inspect
#: and delete. Here rather than in `servers.py` because `lsp.py` needs the path and
#: `servers.py` needs every `lsp.py` backend, which was a cycle.
SERVERS = Path("~/.harness/servers")


def servers_bin() -> Path:
    return (SERVERS / "bin").expanduser()


@runtime_checkable
class CodeIndex(Protocol):
    """One language, over one folder, already rooted.

    Rooted at construction rather than per call: an index is a thing that has read a
    project, and a method that took the root would invite one instance answering for two.
    """

    #: What answered, for logs and for the first line of a result. A model told which
    #: backend replied can weigh the answer; one told nothing cannot.
    name: str
    #: Which files this can speak for, lowercase and dotted -- (".py", ".pyi").
    extensions: tuple[str, ...]

    async def definitions(self, name: str, *, near: Path | None = None) -> tuple[Symbol, ...]:
        """Every symbol `name` could refer to. `near` narrows to one file when known.

        Empty is an ordinary answer, not an error: "is there anything called this" is a
        question, exactly as `Store.load` returning `None` is an answer about a thread that
        is not there. It reaches the model as `ok`, the way `grep` finding nothing does.

        Accepts a dotted name (`Workspace.resolve`) as well as a bare one. How that is
        matched is the implementation's business, like every other foreign shape.
        """
        ...

    async def references(self, symbol: Symbol) -> tuple[Location, ...]:
        """Everywhere that one symbol is used, its definition included.

        Takes the `Symbol` from `definitions` rather than a name -- see the module note.
        The implementation locates the exact column by finding `symbol.name` on
        `symbol.location.line`, rather than trusting a column carried through a model's
        arguments, because a miscounted column is a wrong answer with no symptom.
        """
        ...

    async def aclose(self) -> None:
        """Release whatever this holds. Safe to call twice, and after a crash.

        Reachable on shutdown: `Runtime.aclose` is where a server front end's SIGTERM
        arrives, and a backend holding a subprocess is exactly what that exists for.
        """
        ...


@dataclass
class Indexes:
    """The indexes one workspace has, and which of them answers.

    Held by the tools that use it, the way `Plan` is held by `UpdatePlan`: `ToolContext` is
    `paths` and nothing else on purpose, and a context that grew a field per stateful tool
    would hand every tool everything.

    **Polyglot is the ordinary case, not the exception.** Almost every repository is mixed
    in the trivial sense -- Markdown, TOML, a shell script beside the code -- and many are
    mixed in the real one. So a question with a file attached goes to that file's language,
    and a question without one goes to *all* of them and the answers are merged. Returning
    nothing because two languages were configured, which is what choosing one index would
    do, is the wrong answer to a question that has a right one.
    """

    available: list[CodeIndex] = field(default_factory=list)

    def by_extension(self, extension: str) -> CodeIndex | None:
        return next(
            (i for i in self.available if extension.lower() in i.extensions), None
        )

    def languages(self) -> str:
        return ", ".join(sorted({e for i in self.available for e in i.extensions}))

    async def definitions(self, name: str, near: Path | None = None) -> tuple[Symbol, ...]:
        """Every definition of `name`, from whichever language can speak about it.

        With a file, one index answers. Without one, every index does and the results are
        concatenated -- a name may genuinely be defined in two languages, and a repository
        with a Go service and a Python client is exactly where someone asks.

        One backend failing does not fail the question: a Go server that is not provisioned
        should not hide the Python answer. It is raised only when nothing could answer at
        all, which is the case where the caller has nothing else to go on.
        """
        chosen = self._asked(near)
        if not chosen:
            raise CodeIndexError(self._nothing_for(near), available=False)

        found: list[Symbol] = []
        failures: list[CodeIndexError] = []
        for index in chosen:
            try:
                found.extend(await index.definitions(name, near=near))
            except CodeIndexError as exc:
                failures.append(exc)
        if failures and not found and len(failures) == len(chosen):
            raise failures[0]
        return tuple(found)

    async def references(self, symbol: Symbol) -> tuple[Location, ...]:
        """Uses of one symbol. Never ambiguous: a symbol always carries its file."""
        index = self.by_extension(symbol.location.path.suffix)
        if index is None:
            raise CodeIndexError(
                self._nothing_for(symbol.location.path), available=False
            )
        return await index.references(symbol)

    def _asked(self, near: Path | None) -> list[CodeIndex]:
        if near is not None:
            index = self.by_extension(near.suffix)
            return [index] if index is not None else []
        return list(self.available)

    def _nothing_for(self, near: Path | None) -> str:
        if not self.available:
            return (
                "No code index is set up. Run `harness --install-servers`, or use grep "
                + "and read_file."
            )
        subject = f"{near.suffix} files" if near is not None and near.suffix else "that"
        return f"No code index for {subject}. Indexed here: {self.languages()}. Use grep."

    async def aclose(self) -> None:
        for index in self.available:
            await index.aclose()
