"""Tools, the contract they share, and the approval layer in front of them."""

from __future__ import annotations

import asyncio
from pathlib import Path

import jsonschema
import pytest

from harness.agent.runner import ToolRunner
from harness.providers.openai import decode_message, merge_tool_call_deltas
from harness.settings import Output
from harness.state.approval import Approvals, Decision, Policy, Request, approve_all, deny_all
from harness.tools import Registry, ToolContext, bind, new_registry
from harness.tools.files import file_tools
from harness.tools.shell import Command, Shell, _program
from harness.types import ToolCall, ToolResult, ToolSpec
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
    return new_registry([*file_tools(), bind(Shell())])


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
        new_registry([*file_tools(), *file_tools()])


def test_a_malformed_schema_is_caught_at_registration() -> None:
    class Bad:
        spec = ToolSpec("bad", "d", {"type": "not-a-type"})

        def preview(self, arguments, /):  # pragma: no cover
            return "bad", "bad"

        async def call(self, arguments, ctx, /):  # pragma: no cover
            return ToolResult("")

    with pytest.raises(jsonschema.SchemaError):
        new_registry([Bad()])


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
    assert shell.preview(Command("git status")) == ("run: git status", "run:git")
    assert shell.preview(Command("rm -rf build"))[1] == "run:rm"
    assert bind(shell).preview({"command": "ls -la"})[1] == "run:ls"


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


async def test_a_failing_command_reports_its_exit_code_as_an_answer(
    registry: Registry, ctx: ToolContext
) -> None:
    """A non-zero exit is what the command said, not a tool that could not work. `grep`
    with no matches is `ok` for the same reason."""
    runner = ToolRunner(registry, ctx, Approvals(ask=approve_all))

    result = await runner.run(ToolCall("1", "run", {"command": "exit 3"}))

    assert result.ok
    assert "exit 3" in result.content


async def test_a_hanging_command_is_killed(registry: Registry, ctx: ToolContext) -> None:
    runner = ToolRunner(registry, ctx, Approvals(ask=approve_all))

    result = await runner.run(ToolCall("1", "run", {"command": "sleep 30", "timeout": 1}))

    assert not result.ok
    assert "timed out" in result.content


async def test_a_timeout_does_not_wait_for_a_grandchild_holding_the_pipe(
    registry: Registry, ctx: ToolContext
) -> None:
    """The timeout is a bound on the call, not a suggestion.

    Killing the shell ends the shell and nothing it started. A grandchild that outlives it
    keeps the stdout pipe open, and the harness stays blocked reading a pipe that will never
    reach EOF -- then reports a timeout it did not honour.

    Found live on `07-service`: a `curl` waiting on a server that never sent a response body
    held a 120s timeout open for **2748 seconds**, and the tool result still read "command
    timed out after 120s and was killed". The run resumed two seconds after the server was
    killed by hand. The comment above `_terminate` already said to kill the group; the code
    did not.
    """
    runner = ToolRunner(registry, ctx, Approvals(ask=approve_all))
    loop = asyncio.get_running_loop()

    started = loop.time()
    result = await runner.run(
        ToolCall("1", "run", {"command": "sleep 30 & echo started; wait", "timeout": 1})
    )
    elapsed = loop.time() - started

    assert not result.ok
    assert "timed out" in result.content
    assert elapsed < 10, f"the call outlived its own timeout by {elapsed:.0f}s"


async def test_a_cancelled_command_is_killed_not_orphaned(
    registry: Registry, ctx: ToolContext
) -> None:
    """Ctrl-C must not leave the command running.

    `start_new_session` takes the command out of the harness's process group, which is what
    lets the timeout kill the whole tree -- and also what stops the terminal's SIGINT from
    reaching it. Nothing else would kill it: the CLI's shutdown path closes the provider and
    does nothing about children. So without this, a command interrupted at the keyboard keeps
    running with no parent, which is the exact failure `run` refuses `&` for.
    """
    runner = ToolRunner(registry, ctx, Approvals(ask=approve_all))
    marker = ctx.paths.root / "outlived"

    task = asyncio.create_task(
        runner.run(ToolCall("1", "run", {"command": "sleep 1; touch outlived", "timeout": 60}))
    )
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(1.5)
    assert not marker.exists(), "the command outlived the cancellation of the run that owned it"



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


async def test_a_timeout_is_a_real_tool_failure(
    registry: Registry, ctx: ToolContext
) -> None:
    """The line: a command that ran and said no is an answer; a command that never finished
    is the tool not doing its job."""
    runner = ToolRunner(registry, ctx, Approvals(ask=approve_all))

    result = await runner.run(
        ToolCall("1", "run", {"command": "sleep 30", "timeout": 1})
    )

    assert not result.ok
    assert not result.refused
    assert "timed out" in result.content


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
    long = ToolResult("x" * 100, ok=False, refused=True).truncated(10, Output().split_floor)

    assert long.refused and not long.ok


async def test_a_command_verdict_at_the_tail_survives_the_loop(ctx) -> None:
    """`run` must not truncate: the loop does, and it keeps both ends.

    This regressed once and silently. The shell had its own head-only cut at the same 30k
    as the loop's, so it ran first and won -- and every test runner puts its answer at the
    tail. `pytest` says "5 failed, 200 passed" there, `go test` says `FAIL`. The both-ends
    cut could not save what the shell had already thrown away. One number in two places was
    two rules. (2026-08-31)
    """
    out = Output()
    command = (
        "python3 -c \"print('noise\\n' * 20000); print('FAIL: 5 of 200 tests failed')\""
    )

    result = await bind(Shell()).call({"command": command}, ctx)
    final = result.truncated(out.per_result, out.split_floor)

    assert len(result.content) > out.per_result, "run must hand the loop the whole output"
    assert "FAIL: 5 of 200 tests failed" in final.content


# -- background commands -------------------------------------------------------------------


async def test_a_background_command_answers_with_a_handle_not_its_output(tmp_path) -> None:
    """The handle IS the tool result. That is why later output cannot also be one: the call
    has been answered, and each call is answered once."""
    from harness.exec.processes import Processes
    from harness.state.inbox import Inbox
    from harness.tools.shell import shell_tools

    processes = Processes(inbox=Inbox(), root=tmp_path / "out")
    run, read, stop, *_ = shell_tools(processes=processes)
    ctx = ToolContext(paths=Workspace.at(tmp_path), call_id="call_1")

    started = await run.call({"command": "echo up; sleep 5", "background": True}, ctx)

    assert started.ok
    assert "started (pid" in started.content
    assert "read_process" in started.content
    process_id = started.content.split()[0]
    assert processes.get(process_id) is not None
    assert processes.get(process_id).call_id == "call_1"
    await processes.aclose()


async def test_its_output_is_fetched_and_comes_back_as_a_tool_result(tmp_path) -> None:
    from harness.exec.processes import Processes
    from harness.state.inbox import Inbox
    from harness.tools.shell import shell_tools

    processes = Processes(inbox=Inbox(), root=tmp_path / "out")
    run, read, stop, *_ = shell_tools(processes=processes)
    ctx = ToolContext(paths=Workspace.at(tmp_path))

    started = await run.call({"command": "echo hello", "background": True}, ctx)
    process_id = started.content.split()[0]
    await asyncio.sleep(0.4)
    seen = await read.call({"process_id": process_id}, ctx)

    assert seen.ok and "hello" in seen.content
    await processes.aclose()


async def test_an_exit_puts_a_notice_in_the_inbox_and_never_the_output(tmp_path) -> None:
    """Metadata only. The output is a file the model can read when it wants to; putting it
    here would put text nobody in the conversation wrote into a user-shaped row."""
    from harness.exec.processes import Processes
    from harness.state.inbox import Inbox, Source
    from harness.tools.shell import shell_tools

    box = Inbox()
    processes = Processes(inbox=box, root=tmp_path / "out")
    run, *_ = shell_tools(processes=processes)

    # A command whose OUTPUT does not appear in its own text, so the two can be told apart.
    # The notice quotes the command deliberately -- the model wrote that, and reading it
    # back is not an attribution problem. Its output would be.
    started = await run.call(
        {"command": "echo $((6*7))", "background": True},
        ToolContext(paths=Workspace.at(tmp_path), call_id="call_7"),
    )
    process_id = started.content.split()[0]
    await asyncio.sleep(0.4)
    arrived = box.drain()

    assert len(arrived) == 1
    assert arrived[0].source is Source.HARNESS
    assert "42" not in arrived[0].text          # the output stayed in the file
    assert "echo $((6*7))" in arrived[0].text   # the command it was asked to run did not
    assert "exited 0" in arrived[0].text
    assert arrived[0].call_id == "call_7"
    assert "42" in processes.read(process_id)   # and is there when the model asks for it
    await processes.aclose()


async def test_closing_reaps_what_the_run_started(tmp_path) -> None:
    """An eval that leaves servers running accumulates them, each holding a port."""
    from harness.exec.processes import Processes
    from harness.state.inbox import Inbox
    from harness.tools.shell import shell_tools

    processes = Processes(inbox=Inbox(), root=tmp_path / "out")
    run, *_ = shell_tools(processes=processes)

    started = await run.call(
        {"command": "sleep 60", "background": True}, ToolContext(paths=Workspace.at(tmp_path))
    )
    process = processes.get(started.content.split()[0])
    await processes.aclose()

    assert process.child.returncode is not None
    assert processes.started == {}


# -- watching ------------------------------------------------------------------------------


async def test_a_monitor_reports_its_lines_as_they_arrive(tmp_path) -> None:
    """The one source that carries content, because a notice reading '3 new lines' would
    cost a turn to read every time, which is no watch at all."""
    from harness.exec.processes import Processes
    from harness.state.inbox import Inbox, Source
    from harness.tools.shell import shell_tools

    box = Inbox()
    processes = Processes(inbox=box, root=tmp_path / "out")
    *_, watch, read, stop = shell_tools(processes=processes)
    ctx = ToolContext(paths=Workspace.at(tmp_path), call_id="call_3")

    started = await watch.call(
        {"command": "echo one; sleep 0.5; echo two", "description": "a fake log"}, ctx
    )
    await asyncio.sleep(1.6)
    arrived = box.drain()

    assert started.ok and "monitoring (pid" in started.content
    lines = [e for e in arrived if e.source is Source.MONITOR]
    assert "one" in " ".join(e.text for e in lines)
    assert "two" in " ".join(e.text for e in lines)
    assert all(e.call_id == "call_3" for e in lines)
    # And an ending notice, which is metadata rather than content.
    assert any(e.source is Source.HARNESS and "ended with code 0" in e.text for e in arrived)
    await processes.aclose()


async def test_a_monitor_that_matches_everything_is_stopped(tmp_path) -> None:
    """A filter matching everything is a mistake, and one that would fill the context faster
    than compaction can clear it -- the newest turn is the part kept verbatim."""
    from harness.exec.monitor import FLOOD
    from harness.exec.processes import Processes
    from harness.state.inbox import Inbox
    from harness.tools.shell import shell_tools

    box = Inbox(limit=500)
    processes = Processes(inbox=box, root=tmp_path / "out")
    *_, watch, _, _ = shell_tools(processes=processes)

    started = await watch.call(
        {"command": "while true; do echo spam; done", "description": "a bad filter"},
        ToolContext(paths=Workspace.at(tmp_path)),
    )
    watch_id = started.content.split()[0]
    for _ in range(60):
        await asyncio.sleep(0.1)
        if not processes.started[watch_id].running:
            break

    running = processes.started[watch_id]
    assert not running.running
    assert running.monitor is not None and running.monitor.seen >= FLOOD
    assert any("more than a monitor is allowed" in e.text for e in box.drain())
    await processes.aclose()


async def test_reading_and_stopping_a_monitor_that_is_not_there(tmp_path) -> None:
    from harness.exec.processes import Processes
    from harness.state.inbox import Inbox
    from harness.tools.shell import shell_tools

    processes = Processes(inbox=Inbox(), root=tmp_path / "out")
    *_, _, read, stop = shell_tools(processes=processes)
    ctx = ToolContext(paths=Workspace.at(tmp_path))

    missing = await read.call({"monitor_id": "mon_nope"}, ctx)
    cannot = await stop.call({"monitor_id": "mon_nope"}, ctx)

    assert missing.refused and "no monitor" in missing.content
    assert cannot.refused and "no monitor" in cannot.content


async def test_a_command_that_detaches_itself_is_refused(tmp_path) -> None:
    """Found live, twice. First an agent ran `python3 server.py 18080 &` in the foreground
    and the process outlived its run by nine minutes. Then, with backgrounding available, it
    passed `bash noisy.sh &` WITH background=true -- so the harness registered the wrapper
    shell, saw it exit in 0s, and the real script ran on where nothing could reach it. The
    second is worse, because it looks like it worked."""
    from harness.exec.processes import Processes
    from harness.state.inbox import Inbox
    from harness.tools.shell import shell_tools

    processes = Processes(inbox=Inbox(), root=tmp_path / "out")
    run, *_ = shell_tools(processes=processes)
    ctx = ToolContext(paths=Workspace.at(tmp_path))

    both = await run.call({"command": "bash x.sh &", "background": True}, ctx)
    alone = await run.call({"command": "bash x.sh &"}, ctx)

    assert both.refused and "Remove the `&`" in both.content
    assert alone.refused and "Use background=true instead" in alone.content
    assert processes.started == {}


async def test_the_shapes_that_only_look_like_backgrounding_are_allowed(tmp_path) -> None:
    """`&&` is a conjunction, `2>&1` is a redirection, and a quoted ampersand is text. A
    check that refused those would refuse most real command lines."""
    from harness.tools.shell import _backgrounds

    assert not _backgrounds("make && make test")
    assert not _backgrounds("pytest 2>&1 | tail")
    assert not _backgrounds("cmd &> out.log")
    assert not _backgrounds('echo "a & b"')
    # Two jobs and a wait blocks until both finish, so nothing is orphaned.
    assert not _backgrounds("a & b & wait")
    assert _backgrounds("bash noisy.sh &")
    assert _backgrounds("rm -f app.log && bash noisy.sh &")


async def test_a_monitor_on_a_command_that_just_exits_says_it_was_the_wrong_tool(
    tmp_path,
) -> None:
    """Seen twice against the live model: it wrote `grep -F ERROR file` and `tail -n 1 file`
    as watches, which exit at once and can never send a second notice. The description does
    say to use `run` for those, and saying it there did not work -- it is read once, long
    before the moment it applies. This says it at the moment it applies."""
    from harness.exec.processes import Processes
    from harness.state.inbox import Inbox

    (tmp_path / "app.log").write_text("INFO nothing interesting\n")
    processes = Processes(inbox=(box := Inbox()), root=tmp_path / "out")

    await processes.monitor(
        "grep -F ERROR app.log", "look for errors",
        cwd=tmp_path, env={"PATH": "/usr/bin:/bin"},
    )
    await asyncio.sleep(1.2)
    ending = [e.text for e in box.drain() if "ended with code" in e.text]

    assert ending and "gained you nothing" in ending[-1]
    assert "background=true" in ending[-1]
    await processes.aclose()


async def test_a_monitor_that_actually_streams_is_left_alone(tmp_path) -> None:
    """The check has to be narrow enough that a real watch is never lectured."""
    from harness.exec.processes import Processes
    from harness.state.inbox import Inbox

    processes = Processes(inbox=(box := Inbox()), root=tmp_path / "out")

    await processes.monitor(
        "for i in 1 2 3; do echo line $i; sleep 0.3; done", "a real stream",
        cwd=tmp_path, env={"PATH": "/usr/bin:/bin"},
    )
    await asyncio.sleep(2.0)
    ending = [e.text for e in box.drain() if "ended with code" in e.text]

    assert ending and "gained you nothing" not in ending[-1]
    await processes.aclose()
