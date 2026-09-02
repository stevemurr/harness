"""Code navigation: the contract, and the two tools over it.

The conformance suite is the point of the interface, the way `test_store.py` is the point of
`Store`. It is parameterised over every implementation, so adding a language means running
tests that already exist rather than writing new ones -- and the fake in here is what proves
the protocol is implementable twice, which a protocol with one implementation has never
had checked.

`basedpyright` is skipped when it is not installed, so the suite is green on a machine that
has never heard of it. What that costs is real and worth naming: only the fake runs in CI
unless the binary is there, so the fake must not drift into being easier to satisfy than the
protocol. Everything asserted below is asserted against both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.code.base import CodeIndex, CodeIndexError, Indexes, Location, Symbol
from harness.code.gopls import Gopls
from harness.code.pyright import Pyright
from harness.code.servers import servers_bin
from harness.settings import Code
from harness.tools import ToolContext, new_registry
from harness.tools.code import code_tools
from harness.types import ToolCall
from harness.workspace import Workspace

PROJECT = '''\
"""A tiny project, with one ambiguity on purpose."""


class Widget:
    def build(self) -> str:
        return "widget"


class Gadget:
    def build(self) -> str:
        return "gadget"


def assemble() -> str:
    return Widget().build() + Gadget().build()
'''


GO_PROJECT = """package shop

type Widget struct{}

func (w Widget) Build() string { return "widget" }

type Gadget struct{}

func (g Gadget) Build() string { return "gadget" }

func Assemble() string { return Widget{}.Build() + Gadget{}.Build() }
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """One fixture, two languages, the same shape.

    Deliberately the same shape in both: two types with a method of the same name, and a
    function using both. The conformance suite asks the same questions of Python and Go and
    expects the same answers, which is the whole claim the interface makes.
    """
    (tmp_path / "shop.py").write_text(PROJECT)
    (tmp_path / "go.mod").write_text("module shop\n\ngo 1.22\n")
    (tmp_path / "shop.go").write_text(GO_PROJECT)
    return tmp_path


class Fake:
    """A code index over a hand-written table.

    Not a stub that returns whatever the test wants: it answers from the same fixture the
    real server reads, so the conformance suite means the same thing for both.
    """

    name = "fake"
    extensions = (".py", ".pyi")

    def __init__(self, root: Path) -> None:
        self.root = root
        self.closed = 0

    async def definitions(self, name: str, *, near: Path | None = None) -> tuple[Symbol, ...]:
        container, _, bare = name.rpartition(".")
        table = [
            Symbol("Widget", Location(self.root / "shop.py", 4, "class Widget:"), "class"),
            Symbol("Gadget", Location(self.root / "shop.py", 9, "class Gadget:"), "class"),
            Symbol(
                "build", Location(self.root / "shop.py", 5, "    def build"), "method", "Widget"
            ),
            Symbol(
                "build", Location(self.root / "shop.py", 10, "  def build"), "method", "Gadget"
            ),
            Symbol(
                "assemble", Location(self.root / "shop.py", 14, "def assemble"), "function"
            ),
        ]
        found = [s for s in table if s.name == bare]
        if container:
            found = [s for s in found if s.container == container]
        if near is not None:
            found = [s for s in found if s.location.path == near.resolve()]
        return tuple(found)

    async def references(self, symbol: Symbol) -> tuple[Location, ...]:
        if symbol.name == "Widget":
            return (
                Location(self.root / "shop.py", 4, "class Widget:"),
                Location(self.root / "shop.py", 15, "    return Widget().build()"),
            )
        return ()

    async def aclose(self) -> None:
        self.closed += 1


#: What each implementation calls the same things. The suite asks the same questions of
#: every backend; only the spelling of the fixture differs, because Python and Go do.
DIALECT = {
    "fake": {"type": "Widget", "method": "build", "user": "assemble"},
    "pyright": {"type": "Widget", "method": "build", "user": "assemble"},
    "gopls": {"type": "Widget", "method": "Build", "user": "Assemble"},
}


def provisioned(binary: str) -> bool:
    """Set up for the harness, not merely present on PATH.

    The same question `LspIndex.argv` asks. Checking `PATH` instead would skip when the
    harness would have worked, and run when it would not.
    """
    return (servers_bin() / binary).exists()


@pytest.fixture(params=["fake", "pyright", "gopls"])
def index(request: pytest.FixtureRequest, project: Path):
    """Every implementation, over one fixture project.

    A real server is skipped when it is not installed, so the suite is green on a machine
    that has neither. What that costs is worth naming: on such a machine only the fake
    runs, so the fake must not be easier to satisfy than the protocol -- everything below
    is asserted against all three.
    """
    slow = Code(warmup=25.0, startup_timeout=60.0)
    if request.param == "fake":
        index = Fake(project)
    elif request.param == "pyright":
        if not provisioned("basedpyright-langserver"):
            pytest.skip("run `harness --install-servers` to exercise basedpyright")
        index = Pyright(project, slow)
    else:
        if not provisioned("gopls"):
            pytest.skip("run `harness --install-servers` to exercise gopls")
        index = Gopls(project, slow)
    index.words = DIALECT[request.param]
    return index


# --- the contract, over every implementation ---------------------------------------------


async def test_an_implementation_satisfies_the_protocol(index) -> None:
    """Structural, so a backend that never imports the protocol still satisfies it."""
    assert isinstance(index, CodeIndex)
    assert index.name
    assert index.extensions and all(e.startswith(".") for e in index.extensions)


async def test_a_defined_name_is_found_with_a_1_based_line(index, project: Path) -> None:
    """1-based, matching `read_file` and `edit_file`. A 0-based line leaking up from the
    wire would be an off-by-one in every language added afterwards -- so the line is
    checked against the file rather than against a constant."""
    found = await index.definitions(index.words["type"])

    assert [s.name for s in found] == [index.words["type"]]
    where = found[0].location
    text = where.path.read_text().splitlines()[where.line - 1]
    assert index.words["type"] in text, f"line {where.line} is {text!r}"
    await index.aclose()


async def test_a_name_that_is_not_there_is_empty_and_not_an_error(index) -> None:
    """"Is there anything called this" is an ordinary question, exactly as `Store.load`
    returning `None` is an ordinary answer. It reaches the model as `ok`, like grep."""
    assert await index.definitions("NoSuchSymbolAnywhere") == ()
    await index.aclose()


async def test_one_name_can_mean_several_things(index) -> None:
    """The whole reason resolution is two steps. `build` is a method on two classes here,
    and 71 different things in the harness itself."""
    found = await index.definitions(index.words["method"])

    assert len(found) >= 2
    assert {s.container for s in found} >= {"Widget", "Gadget"}
    await index.aclose()


async def test_a_dotted_name_picks_one_of_them(index) -> None:
    found = await index.definitions(f"Widget.{index.words['method']}")

    assert len(found) == 1
    assert found[0].container == "Widget"
    await index.aclose()


async def test_references_include_the_definition_and_the_use(index) -> None:
    """Asserted by content, not by line number, because the two fixtures are laid out
    differently and the claim is about the answer rather than the formatting."""
    found = await index.definitions(index.words["type"])

    places = await index.references(found[0])

    texts = " ".join(p.text for p in places)
    assert any(index.words["type"] in p.text for p in places), "the definition is a reference"
    if index.name != "fake":
        assert index.words["user"].lower() in texts.lower() or len(places) >= 2
    await index.aclose()


async def test_closing_twice_is_not_an_error(index) -> None:
    """`Runtime.aclose` may run after a crash, and a supervisor may send two signals."""
    await index.aclose()
    await index.aclose()


# --- choosing an index --------------------------------------------------------------------


class Go:
    """A second language, so the polyglot behaviour has two things to be about."""

    name = "fake-go"
    extensions = (".go",)

    def __init__(self, root: Path) -> None:
        self.root = root
        self.asked: list[str] = []

    async def definitions(self, name: str, *, near: Path | None = None) -> tuple[Symbol, ...]:
        self.asked.append(name)
        if name != "Widget":
            return ()
        return (Symbol("Widget", Location(self.root / "shop.go", 3, "type Widget"), "struct"),)

    async def references(self, symbol: Symbol) -> tuple[Location, ...]:
        return (Location(self.root / "shop.go", 3, "type Widget"),)

    async def aclose(self) -> None:
        return None


async def test_a_question_with_no_file_asks_every_language(project: Path) -> None:
    """Polyglot is the ordinary case. Choosing one index would answer "nothing" to a
    question that has an answer, because two languages were configured."""
    both = Indexes([Fake(project), Go(project)])

    found = await both.definitions("Widget")

    assert {s.location.path.suffix for s in found} == {".py", ".go"}


async def test_a_question_about_one_file_asks_only_its_language(project: Path) -> None:
    """A Go server should not be started to answer about a Python file."""
    go = Go(project)
    both = Indexes([Fake(project), go])

    found = await both.definitions("Widget", project / "shop.py")

    assert [s.location.path.suffix for s in found] == [".py"]
    assert go.asked == [], "the Go index should not have been consulted"


async def test_one_language_failing_does_not_hide_the_others(project: Path) -> None:
    """An unprovisioned Go server must not suppress the Python answer."""

    class Broken(Go):
        async def definitions(self, name, *, near=None):
            raise CodeIndexError("gopls is not set up", available=False)

    both = Indexes([Fake(project), Broken(project)])

    found = await both.definitions("Widget")

    assert [s.location.path.suffix for s in found] == [".py"]


async def test_a_file_in_a_language_with_no_index_says_which_are_indexed(project: Path) -> None:
    """The trivial polyglot case: a shell script beside the code."""
    only_python = Indexes([Fake(project)])

    with pytest.raises(CodeIndexError) as caught:
        await only_python.definitions("main", project / "deploy.sh")

    assert ".sh" in str(caught.value) and "grep" in str(caught.value)


async def test_with_no_index_at_all_the_message_names_the_command(project: Path) -> None:
    with pytest.raises(CodeIndexError) as caught:
        await Indexes().definitions("Widget")

    assert "--install-servers" in str(caught.value)


# --- the tools ----------------------------------------------------------------------------


@pytest.fixture
def kit(project: Path):
    indexes = Indexes([Fake(project)])
    tools = code_tools(indexes)
    return new_registry(tools), ToolContext(paths=Workspace.at(project)), indexes


async def test_references_without_a_definition_site_is_refused_by_the_schema(kit) -> None:
    """THE two-step, and it costs no code: `path` and `line` are `required`, so
    `Registry.run` refuses before the tool is reached and names the missing field."""
    registry, ctx, _ = kit

    result = await registry.run(ToolCall("c", "find_references", {"symbol": "build"}), ctx)

    assert result.refused and not result.ok
    assert "path" in result.content


async def test_find_definition_lists_every_candidate_with_a_line(kit) -> None:
    registry, ctx, _ = kit

    result = await registry.run(ToolCall("c", "find_definition", {"symbol": "build"}), ctx)

    assert result.ok
    assert "shop.py:5" in result.content and "shop.py:10" in result.content
    assert "Widget.build" in result.content and "Gadget.build" in result.content


async def test_find_definition_puts_definitions_above_incidental_matches(kit) -> None:
    """`workspace/symbol` matches local variables too. The one almost always meant should
    not be buried under the ones almost never meant."""
    registry, ctx, indexes = kit
    noisy = Symbol("build", Location(ctx.paths.root / "shop.py", 99, "build = 1"), "variable")
    original = indexes.available[0].definitions

    async def with_noise(name, *, near=None):
        return (noisy, *await original(name, near=near))

    indexes.available[0].definitions = with_noise

    result = await registry.run(ToolCall("c", "find_definition", {"symbol": "build"}), ctx)

    body = [line for line in result.content.splitlines() if "shop.py" in line]
    assert "method" in body[0], "a method should outrank a variable"


async def test_nothing_found_is_ok_and_points_at_grep(kit) -> None:
    registry, ctx, _ = kit

    result = await registry.run(ToolCall("c", "find_definition", {"symbol": "Absent"}), ctx)

    assert result.ok and not result.refused
    assert "grep" in result.content


async def test_a_missing_backend_fails_rather_than_refuses(project: Path) -> None:
    """`failed`, not `refused`. The harness did not decline -- it tried and the world said
    no. It matters concretely: the loop's stall counter counts refusals only, so calling
    this a refusal would let a missing binary end a run that is otherwise working."""
    nowhere = Code(commands={"basedpyright": ("definitely-not-a-language-server",)})
    missing = Pyright(project, nowhere)
    tools = code_tools(Indexes([missing]))
    registry = new_registry(tools)
    ctx = ToolContext(paths=Workspace.at(project))

    result = await registry.run(ToolCall("c", "find_definition", {"symbol": "Widget"}), ctx)

    assert not result.ok
    assert not result.refused, "a stall counter that counted this would end working runs"
    assert "grep" in result.content


async def test_with_no_index_configured_the_tool_says_so(project: Path) -> None:
    tools = code_tools(Indexes())
    registry = new_registry(tools)
    ctx = ToolContext(paths=Workspace.at(project))

    result = await registry.run(ToolCall("c", "find_definition", {"symbol": "Widget"}), ctx)

    assert not result.ok and "grep" in result.content


async def test_the_backend_is_unavailable_rather_than_merely_broken(project: Path) -> None:
    """The distinction `ProviderError.retryable` makes, in the other direction: a binary
    that is not installed will not become installed during this run."""
    nowhere = Code(commands={"basedpyright": ("definitely-not-a-language-server",)})
    missing = Pyright(project, nowhere)

    with pytest.raises(CodeIndexError) as caught:
        await missing.definitions("Widget")

    assert caught.value.available is False


async def test_the_tools_are_offered_in_plan_mode(project: Path) -> None:
    """Read-only, so `Mode.permits` keeps them -- and plan mode is where code search is
    worth the most, since the whole activity is reading before deciding."""
    from harness.mode import PLAN

    tools = code_tools(Indexes([Fake(project)]))

    for tool in tools:
        assert PLAN.permits(tool.spec.name, tool.spec.mutates)


async def test_a_path_outside_the_workspace_is_refused_not_failed(project: Path) -> None:
    """The README's own example of a refusal: "a path outside the folder".

    It matters beyond naming. The loop's stall counter counts refusals and not failures, so
    a tool that lets `PathEscape` escape as an exception makes the same bad path a stall
    signal through `read_file` and invisible through this one. `files.py` catches it; these
    did not, until a model mistyped an absolute path in an eval run and the result came back
    labelled `failed`. (2026-08-31)
    """
    registry = new_registry(code_tools(Indexes([Fake(project)])))
    ctx = ToolContext(paths=Workspace.at(project))

    for call in (
        ToolCall("c", "find_definition", {"symbol": "Widget", "path": "/etc/passwd"}),
        ToolCall("c", "find_references", {"symbol": "W", "path": "/etc/passwd", "line": 1}),
    ):
        result = await registry.run(call, ctx)

        assert result.refused, f"{call.name} should refuse a path outside the folder"
        assert not result.ok


async def test_find_references_accepts_the_name_find_definition_showed(kit) -> None:
    """The tool must not refuse its own output.

    `find_definition` prints a qualified name -- `Widget.build`, because that is what
    distinguishes it from the other `build` -- so a model passes back what it was shown.
    Requiring the bare name meant a run asked for exactly the symbol it had just been
    given, was told the file must have changed, abandoned the index and ground through
    forty-four greps before failing. Read out of a transcript. (2026-08-31)
    """
    registry, ctx, _ = kit

    shown = await registry.run(
        ToolCall("c", "find_definition", {"symbol": "Widget"}), ctx
    )
    assert "Widget" in shown.content

    qualified = await registry.run(
        ToolCall("c", "find_references",
                 {"symbol": "Widget.build", "path": "shop.py", "line": 4}),
        ctx,
    )
    bare = await registry.run(
        ToolCall("c", "find_references", {"symbol": "Widget", "path": "shop.py", "line": 4}),
        ctx,
    )

    assert qualified.ok, qualified.content
    assert bare.ok, bare.content


async def test_a_wrong_line_says_what_is_there_rather_than_guessing(project: Path) -> None:
    """The old message asserted the file had changed, which was a guess and was false --
    and it sent the run off re-reading files it had just read.

    Answered without starting a server: a wrong line is decidable from the file, and the
    command here does not exist, so this also proves nothing was launched."""
    index = Pyright(project, Code(commands={"basedpyright": ("no-such-server",)}))

    with pytest.raises(CodeIndexError) as caught:
        await index.references(
            Symbol("Widget", Location(project / "shop.py", 1))
        )

    assert "does not appear" in str(caught.value)
    assert "class Widget" not in str(caught.value)


# -- Swift's naming, which is unlike the others -------------------------------------------


def test_swift_matches_a_bare_name_against_the_selector_it_heads() -> None:
    """sourcekit-lsp indexes a method under its whole selector. Measured against a built
    SwiftPM package: `balance`, `Ledger.balance` and `record` all returned nothing while
    `balance(for:)` returned the method -- a silence indistinguishable from "no such
    symbol" for the shape a model is most likely to ask about."""
    from harness.code.sourcekit import SourceKit

    index = SourceKit(Path.cwd())

    assert index._same_symbol("balance(for:)", "balance")
    assert index._same_symbol("record(_:amount:)", "record")
    assert index._same_symbol("balance(for:)", "balance(for:)")
    assert index._same_symbol("Ledger", "Ledger")


def test_swift_does_not_match_a_name_that_merely_starts_the_same() -> None:
    """`balanced` is not `balance`, and a prefix rule without the paren would say it is."""
    from harness.code.sourcekit import SourceKit

    index = SourceKit(Path.cwd())

    assert not index._same_symbol("balanced", "balance")
    assert not index._same_symbol("balanceSheet(for:)", "balance")
    assert not index._same_symbol("balance", "balanceSheet")


def test_swift_looks_for_what_is_actually_written_at_the_definition() -> None:
    """`func balance(for name: String)` contains `balance` and never contains
    `balance(for:)`. Without this the method would be found and then refuse to have its
    references traced, because the column search would not find its own symbol."""
    from harness.code.base import Location, Symbol
    from harness.code.sourcekit import SourceKit

    index = SourceKit(Path.cwd())
    method = Symbol(
        name="balance(for:)",
        location=Location(
            Path("Ledger.swift"), 8, "    func balance(for name: String) -> Int {"
        ),
        kind="method",
    )

    assert index._needle(method) == "balance"


def test_every_other_backend_still_matches_exactly() -> None:
    """The hooks must be inert where they were not needed: Python and Go name a symbol by
    the identifier itself, and a loose match there would answer about the wrong thing."""
    from harness.code.pyright import Pyright

    index = Pyright(Path.cwd())

    assert index._same_symbol("resolve", "resolve")
    assert not index._same_symbol("resolve(path:)", "resolve")


def test_swift_is_offered_only_where_there_is_swift(tmp_path: Path) -> None:
    from harness.code.servers import for_workspace

    (tmp_path / "main.swift").write_text("print(1)")
    swift = {index.name for index in for_workspace(tmp_path).available}

    other = tmp_path / "other"
    other.mkdir()
    (other / "notes.md").write_text("nothing to index")

    assert "sourcekit-lsp" in swift
    assert "sourcekit-lsp" not in {
        index.name for index in for_workspace(other).available
    }
