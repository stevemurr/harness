"""The board, as four tools. Post, list, claim, finish.

None of them is asked about: a task is a note about work, not a change to the machine, and
a prompt on every one is the approval fatigue that makes people stop reading prompts. The
one thing a tool here holds besides the board is who it is speaking as -- the kit's
identity -- so a claim has an owner without the model having to say its own name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from harness.state.board import Board, Status, Task
from harness.tools.base import Arguments, Handler, ToolContext, bind, spec_for
from harness.types import ToolResult, ToolSpec


@dataclass(frozen=True, slots=True)
class Posting(Arguments):
    title: Annotated[str, "The unit of work, in a line: an outcome, not a tool call."]
    detail: Annotated[str, "What someone picking it up needs to know."] = ""
    assign_to: Annotated[str, "An agent id this is for, or empty for anyone."] = ""
    depends_on: Annotated[list[str], "Task ids that must be done first."] = field(
        default_factory=list
    )


@dataclass(frozen=True, slots=True)
class Listing(Arguments):
    status: Annotated[
        str, "open, claimed, done or failed. Empty lists everything."
    ] = ""


@dataclass(frozen=True, slots=True)
class TaskRef(Arguments):
    task_id: Annotated[str, "The id from list_tasks or post_task, like task_1a2b3c4d."]


@dataclass(frozen=True, slots=True)
class Finishing(Arguments):
    task_id: Annotated[str, "The id you claimed."]
    result: Annotated[str, "What was done, or what went wrong. A line or two."] = ""
    failed: Annotated[bool, "True if the work could not be done."] = False


def _shown(task: Task) -> str:
    lines = [task.line()]
    if task.detail:
        lines.append(f"    {task.detail}")
    if task.result:
        lines.append(f"    result: {task.result}")
    return "\n".join(lines)


@dataclass
class PostTask:
    board: Board
    identity: str
    spec: ToolSpec = field(
        default=spec_for(
            Posting,
            name="post_task",
            description=(
                "Put a unit of work on this folder's board for an agent -- yourself, one "
                + "you delegated to, or one that has not started yet -- to claim. The board "
                + "outlives this run. Use it to split work that more than one agent will do, "
                + "or to leave work for later; use update_plan for your own checklist."
            ),
        )
    )

    async def run(self, args: Posting, _ctx: ToolContext, /) -> ToolResult:
        if not args.title.strip():
            return ToolResult("a task needs a title", ok=False, refused=True)
        task = await self.board.post(
            args.title,
            by=self.identity,
            detail=args.detail,
            assign_to=args.assign_to,
            depends_on=tuple(args.depends_on),
        )
        return ToolResult(f"posted {task.task_id}: {task.title}")


@dataclass
class ListTasks:
    board: Board
    spec: ToolSpec = field(
        default=spec_for(
            Listing,
            name="list_tasks",
            description="What is on this folder's board, with who holds each task.",
        )
    )

    async def run(self, args: Listing, _ctx: ToolContext, /) -> ToolResult:
        status: Status | None = None
        if args.status:
            try:
                status = Status(args.status)
            except ValueError:
                wanted = ", ".join(s.value for s in Status)
                return ToolResult(
                    f"no status {args.status!r}; one of {wanted}", ok=False, refused=True
                )
        tasks = await self.board.list(status)
        if not tasks:
            what = f" that is {status.value}" if status else ""
            return ToolResult(f"nothing on the board{what}")
        return ToolResult("\n".join(_shown(t) for t in tasks))


@dataclass
class ClaimTask:
    board: Board
    identity: str
    spec: ToolSpec = field(
        default=spec_for(
            TaskRef,
            name="claim_task",
            description=(
                "Take a task from the board. Refused if it is held, done, for someone "
                + "else, or waiting on a task that is not done."
            ),
        )
    )

    async def run(self, args: TaskRef, _ctx: ToolContext, /) -> ToolResult:
        claimed = await self.board.claim(args.task_id, by=self.identity)
        if isinstance(claimed, str):
            return ToolResult(claimed, ok=False, refused=True)
        shown = f"claimed {claimed.task_id}: {claimed.title}\n{claimed.detail}"
        return ToolResult(shown.strip())


@dataclass
class FinishTask:
    board: Board
    identity: str
    spec: ToolSpec = field(
        default=spec_for(
            Finishing,
            name="finish_task",
            description=(
                "Mark a task you claimed as done, or as failed with why. Only its holder "
                + "may finish it."
            ),
        )
    )

    async def run(self, args: Finishing, _ctx: ToolContext, /) -> ToolResult:
        finished = await self.board.finish(
            args.task_id, by=self.identity, result=args.result, failed=args.failed
        )
        if isinstance(finished, str):
            return ToolResult(finished, ok=False, refused=True)
        return ToolResult(f"{finished.task_id} {finished.status.value}")


def board_tools(board: Board, identity: str) -> list[Handler]:
    return [
        bind(PostTask(board, identity)),
        bind(ListTasks(board)),
        bind(ClaimTask(board, identity)),
        bind(FinishTask(board, identity)),
    ]
