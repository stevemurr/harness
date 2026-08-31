"""Tools, the contract they share, and the approval layer in front of them."""

from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest

from harness.approval import Approvals, Decision, Policy, Request, approve_all, deny_all
from harness.providers.openai import decode_message, merge_tool_call_deltas
from harness.runner import ToolRunner, describe
from harness.tools.base import Registry, ToolContext, ToolSpec
from harness.tools.files import file_tools
from harness.tools.shell import Shell, _program
from harness.types import ToolCall, ToolResult
from harness.workspace import PathEscape, PathRefused, Workspace, WorkspaceError


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\nprint('bye')\n")
    (tmp_path / "notes.md").write_text("# notes\n")
    return Workspace.at(tmp_path)


@pytest.fixture
def ctx(ws: Workspace) -> ToolContext:
    return ToolContext(paths=ws)


@pytest.fixture
def registry() -> Registry:
    return Registry([*file_tools(), Shell()])


# --- the workspace boundary ------------------------------------------------------------


def test_a_relative_path_resolves_under_the_root(ws: Workspace) -> None:
    assert ws.resolve("src/main.py") == ws.root / "src" / "main.py"


def test_the_root_itself_resolves(ws: Workspace) -> None:
    """`normpath('.')` is `'.'`, and joining that on gives `/ws/.` -- which is not equal to
    the root and fails an equality-based containment test. The predecessor shipped that."""
    assert ws.resolve(".") == ws.root


def test_a_traversal_is_refused(ws: Workspace) -> None:
    with pytest.raises(PathEscape):
        ws.resolve("../outside.txt")


def test_an_absolute_path_outside_is_refused(ws: Workspace) -> None:
    with pytest.raises(PathEscape):
        ws.resolve("/etc/passwd")


def test_a_symlink_out_of_the_tree_is_refused(ws: Workspace, tmp_path: Path) -> None:
    """Resolve first, compare second: matching on the name the model typed misses this."""
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    (ws.root / "gateway").symlink_to(outside)

    with pytest.raises(PathEscape):
        ws.resolve("gateway/escaped.txt")


def test_a_nul_byte_is_a_path_error_not_a_crash(ws: Workspace) -> None:
    """JSON permits it, so a model can send one."""
    with pytest.raises(WorkspaceError):
        ws.resolve("a\x00b")


def test_a_protected_path_is_readable_but_not_writable(tmp_path: Path) -> None:
    state = tmp_path / ".harness"
    state.mkdir()
    (state / "log.jsonl").write_text("the record\n")
    ws = Workspace.at(tmp_path, protected=(state,))

    assert ws.read(".harness/log.jsonl") == "the record\n"
    with pytest.raises(PathRefused):
        ws.write(".harness/log.jsonl", "rewritten")
    assert (state / "log.jsonl").read_text() == "the record\n"


def test_writing_through_a_symlink_is_refused(ws: Workspace, tmp_path: Path) -> None:
    target = tmp_path.parent / "target.txt"
    target.write_text("original\n")
    (ws.root / "link.txt").symlink_to(target)

    with pytest.raises(WorkspaceError):
        ws.write("link.txt", "hijacked")
    assert target.read_text() == "original\n"


# --- the tool contract -----------------------------------------------------------------


async def test_an_unknown_tool_is_a_readable_failure(
    registry: Registry, ctx: ToolContext
) -> None:
    result = await registry.run(ToolCall("1", "teleport", {}), ctx)

    assert not result.ok
    assert "no tool named" in result.content
    assert "read_file" in result.content  # it is told what does exist


async def test_bad_arguments_name_the_field(registry: Registry, ctx: ToolContext) -> None:
    """A tool never sees these, so no tool needs defensive parsing."""
    result = await registry.run(ToolCall("1", "read_file", {}), ctx)

    assert not result.ok
    assert "invalid arguments" in result.content
    assert "path" in result.content


async def test_an_invented_argument_is_refused_rather_than_ignored(
    registry: Registry, ctx: ToolContext
) -> None:
    """Silently dropping it reads to the model as the tool having honoured it."""
    result = await registry.run(
        ToolCall("1", "read_file", {"path": "notes.md", "encoding": "rot13"}), ctx
    )

    assert not result.ok
    assert "invalid arguments" in result.content


def test_two_tools_cannot_share_a_name() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        Registry([*file_tools(), *file_tools()])


def test_a_malformed_schema_is_caught_at_registration() -> None:
    class Bad:
        spec = ToolSpec("bad", "d", {"type": "not-a-type"})

        async def run(self, args, ctx):  # pragma: no cover
            return ToolResult("")

    with pytest.raises(jsonschema.SchemaError):
        Registry([Bad()])


# --- file tools ------------------------------------------------------------------------


async def test_read_file_numbers_lines_from_one(registry: Registry, ctx: ToolContext) -> None:
    result = await registry.run(ToolCall("1", "read_file", {"path": "src/main.py"}), ctx)

    assert result.ok
    assert "     1\tprint('hi')" in result.content


async def test_write_then_read_round_trips(registry: Registry, ctx: ToolContext) -> None:
    await registry.run(
        ToolCall("1", "write_file", {"path": "new.txt", "content": "hello"}), ctx
    )
    result = await registry.run(ToolCall("2", "read_file", {"path": "new.txt"}), ctx)

    assert "hello" in result.content


async def test_an_ambiguous_edit_is_refused_rather_than_guessed(
    registry: Registry, ctx: ToolContext
) -> None:
    """Replacing the first of several edits a line the model never looked at."""
    result = await registry.run(
        ToolCall("1", "edit_file", {"path": "src/main.py", "old": "print", "new": "log"}), ctx
    )

    assert not result.ok
    assert "appears 2 times" in result.content
    assert "print('hi')" in (ctx.paths.root / "src" / "main.py").read_text()


async def test_replace_all_makes_an_ambiguous_edit_explicit(
    registry: Registry, ctx: ToolContext
) -> None:
    result = await registry.run(
        ToolCall(
            "1",
            "edit_file",
            {"path": "src/main.py", "old": "print", "new": "log", "replace_all": True},
        ),
        ctx,
    )

    assert result.ok
    assert "print" not in (ctx.paths.root / "src" / "main.py").read_text()


async def test_an_edit_that_matches_nothing_says_to_read_the_file(
    registry: Registry, ctx: ToolContext
) -> None:
    result = await registry.run(
        ToolCall("1", "edit_file", {"path": "src/main.py", "old": "absent", "new": "x"}), ctx
    )

    assert not result.ok
    assert "does not contain" in result.content


async def test_grep_reports_path_and_line_number(registry: Registry, ctx: ToolContext) -> None:
    result = await registry.run(ToolCall("1", "grep", {"pattern": "bye"}), ctx)

    assert "src/main.py:2:" in result.content


async def test_a_bad_regex_is_the_models_failure_not_a_crash(
    registry: Registry, ctx: ToolContext
) -> None:
    result = await registry.run(ToolCall("1", "grep", {"pattern": "([unclosed"}), ctx)

    assert not result.ok
    assert "bad regular expression" in result.content


async def test_glob_finds_by_pattern(registry: Registry, ctx: ToolContext) -> None:
    result = await registry.run(ToolCall("1", "glob", {"pattern": "*.py"}), ctx)

    assert "src/main.py" in result.content


# --- approvals -------------------------------------------------------------------------


async def test_a_read_only_tool_is_never_asked_about(
    registry: Registry, ctx: ToolContext
) -> None:
    asked: list[Request] = []

    async def record(request: Request) -> Decision:
        asked.append(request)
        return Decision.ALLOW

    runner = ToolRunner(registry, ctx, Approvals(ask=record))
    result = await runner.run(ToolCall("1", "read_file", {"path": "notes.md"}))

    assert result.ok
    assert asked == []


async def test_a_mutating_tool_is_asked_about(
    registry: Registry, ctx: ToolContext
) -> None:
    asked: list[Request] = []

    async def record(request: Request) -> Decision:
        asked.append(request)
        return Decision.ALLOW

    runner = ToolRunner(registry, ctx, Approvals(ask=record))
    await runner.run(ToolCall("1", "write_file", {"path": "x.txt", "content": "hi"}))

    assert len(asked) == 1
    assert asked[0].summary == "write x.txt (2 bytes)"


async def test_a_denial_is_a_readable_result_and_nothing_happens(
    registry: Registry, ctx: ToolContext
) -> None:
    runner = ToolRunner(registry, ctx, Approvals(ask=deny_all))

    result = await runner.run(ToolCall("1", "write_file", {"path": "x.txt", "content": "hi"}))

    assert not result.ok
    assert "declined" in result.content
    assert not (ctx.paths.root / "x.txt").exists()


async def test_no_approver_configured_fails_closed(
    registry: Registry, ctx: ToolContext
) -> None:
    """Silence is not consent."""
    runner = ToolRunner(registry, ctx, Approvals(ask=None))

    result = await runner.run(ToolCall("1", "write_file", {"path": "x.txt", "content": "hi"}))

    assert not result.ok
    assert "no approver is configured" in result.content
    assert not (ctx.paths.root / "x.txt").exists()


async def test_allow_always_stops_asking_for_the_rest_of_the_session(
    registry: Registry, ctx: ToolContext
) -> None:
    asked: list[Request] = []

    async def once(request: Request) -> Decision:
        asked.append(request)
        return Decision.ALLOW_ALWAYS

    runner = ToolRunner(registry, ctx, Approvals(ask=once))
    await runner.run(ToolCall("1", "write_file", {"path": "a.txt", "content": "1"}))
    await runner.run(ToolCall("2", "write_file", {"path": "b.txt", "content": "2"}))

    assert len(asked) == 1
    assert (ctx.paths.root / "b.txt").read_text() == "2"


async def test_a_standing_policy_rule_skips_the_question(
    registry: Registry, ctx: ToolContext
) -> None:
    asked: list[Request] = []

    async def record(request: Request) -> Decision:
        asked.append(request)
        return Decision.DENY

    runner = ToolRunner(
        registry, ctx, Approvals(policy=Policy(always_allow={"write_file"}), ask=record)
    )
    result = await runner.run(ToolCall("1", "write_file", {"path": "x.txt", "content": "hi"}))

    assert result.ok
    assert asked == []


async def test_approve_everything_asks_nothing(registry: Registry, ctx: ToolContext) -> None:
    runner = ToolRunner(
        registry, ctx, Approvals(policy=Policy(approve_everything=True), ask=deny_all)
    )

    result = await runner.run(ToolCall("1", "write_file", {"path": "x.txt", "content": "hi"}))

    assert result.ok


def test_a_shell_grant_covers_the_program_not_the_command_line() -> None:
    """Approving `git status` must not silently approve `git push --force`... but it does
    cover other git. The line is drawn at the program deliberately; approving whole command
    lines would never match twice."""
    shell = Shell()
    assert shell.preview({"command": "git status"}) == ("run: git status", "run:git")
    assert shell.preview({"command": "rm -rf build"})[1] == "run:rm"
    assert describe(shell, {"command": "ls -la"})[1] == "run:ls"


def test_the_program_is_lexed_not_split() -> None:
    assert _program("'/usr/local/my tools/fmt' --check") == "fmt"
    assert _program("FOO=1 BAR=2 pytest -q") == "pytest"
    assert _program("git status") == "git"
    # Unlexable: keyed by the whole string, which simply never matches a grant.
    assert _program('echo "unterminated') == 'echo "unterminated'


# --- shell -----------------------------------------------------------------------------


async def test_a_command_runs_in_the_workspace(registry: Registry, ctx: ToolContext) -> None:
    runner = ToolRunner(registry, ctx, Approvals(ask=approve_all))

    result = await runner.run(ToolCall("1", "run", {"command": "ls"}))

    assert result.ok
    assert "notes.md" in result.content


async def test_a_failing_command_reports_its_exit_code(
    registry: Registry, ctx: ToolContext
) -> None:
    runner = ToolRunner(registry, ctx, Approvals(ask=approve_all))

    result = await runner.run(ToolCall("1", "run", {"command": "exit 3"}))

    assert not result.ok
    assert "exit 3" in result.content


async def test_a_hanging_command_is_killed(registry: Registry, ctx: ToolContext) -> None:
    runner = ToolRunner(registry, ctx, Approvals(ask=approve_all))

    result = await runner.run(ToolCall("1", "run", {"command": "sleep 30", "timeout": 1}))

    assert not result.ok
    assert "timed out" in result.content


# --- provider parsing ------------------------------------------------------------------


def test_a_reply_with_only_tool_calls_has_empty_content_not_none() -> None:
    """`content` is null whenever a model returns only tool calls, which is the normal case
    for a working agent."""
    message = decode_message(
        {
            "content": None,
            "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": '{"path":"a"}'}}
            ],
        }
    )

    assert message.content == ""
    assert message.tool_calls[0].name == "read_file"
    assert message.tool_calls[0].arguments == {"path": "a"}


def test_a_nameless_tool_call_is_dropped_rather_than_left_unanswerable() -> None:
    message = decode_message({"tool_calls": [{"id": "c1", "function": {"arguments": "{}"}}]})

    assert message.tool_calls == ()


def test_streamed_argument_shards_are_joined_by_index() -> None:
    """Providers send id and name once, then arguments in pieces carrying only `index`.
    Keying by id loses every shard after the first."""
    calls = merge_tool_call_deltas(
        [
            {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "write_file"}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": '{"path":'}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": '"a.txt",'}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": '"content":"hi"}'}}]},
        ]
    )

    assert len(calls) == 1
    assert calls[0].call_id == "c1"
    assert calls[0].arguments == {"path": "a.txt", "content": "hi"}


def test_two_interleaved_streamed_calls_stay_separate() -> None:
    calls = merge_tool_call_deltas(
        [
            {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "read_file"}}]},
            {"tool_calls": [{"index": 1, "id": "b", "function": {"name": "list_dir"}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": '{"path":"x"}'}}]},
            {"tool_calls": [{"index": 1, "function": {"arguments": '{"path":"y"}'}}]},
        ]
    )

    assert [c.name for c in calls] == ["read_file", "list_dir"]
    assert calls[0].arguments == {"path": "x"}
    assert calls[1].arguments == {"path": "y"}


# --- refused vs failed -------------------------------------------------------------------


async def test_an_unknown_tool_is_refused_not_failed(
    registry: Registry, ctx: ToolContext
) -> None:
    """The harness declined to act. Nothing was attempted."""
    result = await registry.run(ToolCall("1", "teleport", {}), ctx)

    assert not result.ok
    assert result.refused


async def test_bad_arguments_are_refused_not_failed(
    registry: Registry, ctx: ToolContext
) -> None:
    result = await registry.run(ToolCall("1", "read_file", {}), ctx)

    assert result.refused


async def test_a_path_outside_the_folder_is_refused(
    registry: Registry, ctx: ToolContext
) -> None:
    result = await registry.run(
        ToolCall("1", "write_file", {"path": "/etc/nope", "content": "x"}), ctx
    )

    assert result.refused


async def test_a_missing_file_failed_but_was_not_refused(
    registry: Registry, ctx: ToolContext
) -> None:
    """The harness tried. The world said no. That is ordinary work, not a boundary."""
    result = await registry.run(ToolCall("1", "read_file", {"path": "absent.py"}), ctx)

    assert not result.ok
    assert not result.refused


async def test_a_command_that_exits_nonzero_failed_but_was_not_refused(
    registry: Registry, ctx: ToolContext
) -> None:
    """Five of six `run` failures in one eval round were pytest exiting 1 while the model
    iterated on its own tests -- the loop working. A metric that cannot tell that from a
    refusal reports normal work as breakage. (2026-08-31)"""
    runner = ToolRunner(registry, ctx, Approvals(ask=approve_all))

    result = await runner.run(ToolCall("1", "run", {"command": "exit 3"}))

    assert not result.ok
    assert not result.refused


async def test_a_declined_approval_is_refused(
    registry: Registry, ctx: ToolContext
) -> None:
    runner = ToolRunner(registry, ctx, Approvals(ask=deny_all))

    result = await runner.run(ToolCall("1", "run", {"command": "ls"}))

    assert result.refused


def test_a_result_cannot_be_both_ok_and_refused() -> None:
    """Two booleans can encode a state that means nothing; this one cannot be built."""
    with pytest.raises(ValueError, match="cannot also be ok"):
        ToolResult("x", ok=True, refused=True)


def test_truncation_keeps_the_refusal_flag() -> None:
    """The loop truncates every result, and a flag lost there would be lost everywhere."""
    long = ToolResult("x" * 100, ok=False, refused=True).truncated(10)

    assert long.refused and not long.ok
