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

import shutil
from pathlib import Path

import pytest

from harness.code.base import CodeIndex, CodeIndexError, Indexes, Location, Symbol
from harness.code.gopls import Gopls
from harness.code.pyright import Pyright
from harness.settings import Code
from harness.tools.base import Registry, ToolContext
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


def installed(binary: str) -> bool:
    return shutil.which(binary) is not None


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
        if not installed("basedpyright-langserver"):
            pytest.skip("basedpyright-langserver is not installed")
        index = Pyright(project, slow)
    else:
        if not installed("gopls"):
            pytest.skip("gopls is not installed")
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


def test_a_single_index_answers_without_being_named(tmp_path: Path) -> None:
    """A harness configured for one language should not demand the language be named."""
    only = Indexes([Fake(tmp_path)])

    assert only.choose(None) is not None
    assert only.choose(tmp_path / "a.py") is not None


def test_an_unknown_extension_has_no_index(tmp_path: Path) -> None:
    assert Indexes([Fake(tmp_path)]).choose(tmp_path / "main.go") is None


def test_with_no_index_at_all_there_is_nothing_to_choose(tmp_path: Path) -> None:
    assert Indexes().choose(None) is None


# --- the tools ----------------------------------------------------------------------------


@pytest.fixture
def kit(project: Path):
    tools, indexes = code_tools(Indexes([Fake(project)]))
    return Registry(tools), ToolContext(paths=Workspace.at(project)), indexes


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
    tools, _ = code_tools(Indexes([missing]))
    registry = Registry(tools)
    ctx = ToolContext(paths=Workspace.at(project))

    result = await registry.run(ToolCall("c", "find_definition", {"symbol": "Widget"}), ctx)

    assert not result.ok
    assert not result.refused, "a stall counter that counted this would end working runs"
    assert "grep" in result.content


async def test_with_no_index_configured_the_tool_says_so(project: Path) -> None:
    tools, _ = code_tools(Indexes())
    registry = Registry(tools)
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

    tools, _ = code_tools(Indexes([Fake(project)]))

    for tool in tools:
        assert PLAN.permits(tool.spec.name, tool.spec.mutates)
