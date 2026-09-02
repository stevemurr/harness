"""The work board: units of work, who holds them, and how they went.

A different primitive from the inbox, and the difference is the whole design. The inbox is
messages -- ordered, transient, consumed by `drain`. A board is state: durable units with a
status and an owner, observed rather than consumed. The plan is a third thing, one agent's
private breakdown of the unit it is on. The rule that keeps them apart: the board holds
units of work and their status, threads hold the conversation about each, and the plan
stays the agent's own.

Every harness that has one grew it at the moment there were several workers, and this one
follows: it arrived with `delegate`, and its readers are a parent, its children, and a
person posting work for a run that has not started yet. One board per workspace, keyed by
the folder, because that is the unit a person thinks in.

No push. An agent that wants to know what is on the board asks; a parent hears about its
children through their reports and their finishing notices, which already reach its inbox.
Delivery of board changes into inboxes is the next step if a measurement asks for it, and
would go through `HARNESS` envelopes so the loop stays ignorant of the board the way it is
ignorant of every other source.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import blake2s
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4

from harness.types import JSON, as_list, as_str


class Status(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Task:
    """One unit of work. Immutable; the board replaces it on every change."""

    task_id: str
    title: str
    status: Status = Status.OPEN
    detail: str = ""
    #: Who posted it: an agent's identity, or a person's name from a front end.
    posted_by: str = ""
    #: Who may claim it, or empty for anyone.
    assigned_to: str = ""
    #: Who holds it now. Empty until claimed.
    owner: str = ""
    #: Tasks that must be done before this one may be claimed.
    depends_on: tuple[str, ...] = ()
    #: What the owner said on finishing it.
    result: str = ""
    posted_at: str = ""
    updated_at: str = ""

    def line(self) -> str:
        """One row for a listing."""
        who = ""
        if self.owner:
            who = f" @{self.owner}"
        elif self.assigned_to:
            who = f" for {self.assigned_to}"
        after = f" after {','.join(self.depends_on)}" if self.depends_on else ""
        return f"{self.task_id}  [{self.status.value}]{who}{after}  {self.title}"

    def wire(self) -> JSON:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status.value,
            "detail": self.detail,
            "posted_by": self.posted_by,
            "assigned_to": self.assigned_to,
            "owner": self.owner,
            "depends_on": list(self.depends_on),
            "result": self.result,
            "posted_at": self.posted_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def read(cls, row: JSON) -> Task:
        try:
            status = Status(as_str(row.get("status")))
        except ValueError:
            status = Status.OPEN
        return cls(
            task_id=as_str(row.get("task_id")),
            title=as_str(row.get("title")),
            status=status,
            detail=as_str(row.get("detail")),
            posted_by=as_str(row.get("posted_by")),
            assigned_to=as_str(row.get("assigned_to")),
            owner=as_str(row.get("owner")),
            depends_on=tuple(as_str(d) for d in as_list(row.get("depends_on"))),
            result=as_str(row.get("result")),
            posted_at=as_str(row.get("posted_at")),
            updated_at=as_str(row.get("updated_at")),
        )


@runtime_checkable
class Board(Protocol):
    """Units of work for one workspace. Every change is a whole new `Task`."""

    async def post(
        self,
        title: str,
        *,
        by: str,
        detail: str = "",
        assign_to: str = "",
        depends_on: tuple[str, ...] = (),
    ) -> Task: ...

    async def claim(self, task_id: str, *, by: str) -> Task | str:
        """The task, now held by `by` -- or a sentence saying why it cannot be."""
        ...

    async def finish(
        self, task_id: str, *, by: str, result: str = "", failed: bool = False
    ) -> Task | str:
        """The task, done or failed -- or why not. Only its owner may finish it."""
        ...

    async def get(self, task_id: str) -> Task | None: ...

    async def list(self, status: Status | None = None) -> list[Task]: ...


def board_id_for(root: Path) -> str:
    """One board per folder, derived from the path so it survives a restart with no table."""
    return f"board_{blake2s(str(root.resolve()).encode(), digest_size=8).hexdigest()}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class MemoryBoard:
    """The rules, in memory. `store/boards.py` adds the file; the rules live here once."""

    tasks: dict[str, Task] = field(default_factory=dict)

    async def post(
        self,
        title: str,
        *,
        by: str,
        detail: str = "",
        assign_to: str = "",
        depends_on: tuple[str, ...] = (),
    ) -> Task:
        task = Task(
            task_id=f"task_{uuid4().hex[:8]}",
            title=title.strip(),
            detail=detail.strip(),
            posted_by=by,
            assigned_to=assign_to,
            depends_on=tuple(d for d in depends_on if d),
            posted_at=_now(),
            updated_at=_now(),
        )
        await self._put(task)
        return task

    async def claim(self, task_id: str, *, by: str) -> Task | str:
        task = self.tasks.get(task_id)
        if task is None:
            return f"no task {task_id!r}"
        if task.status is not Status.OPEN:
            held = f" by {task.owner}" if task.owner else ""
            return f"{task_id} is {task.status.value}{held}"
        if task.assigned_to and task.assigned_to != by:
            return f"{task_id} is for {task.assigned_to}"
        waiting = [
            d
            for d in task.depends_on
            if (t := self.tasks.get(d)) is None or t.status is not Status.DONE
        ]
        if waiting:
            return f"{task_id} is blocked on {', '.join(waiting)}"
        claimed = replace(task, status=Status.CLAIMED, owner=by, updated_at=_now())
        await self._put(claimed)
        return claimed

    async def finish(
        self, task_id: str, *, by: str, result: str = "", failed: bool = False
    ) -> Task | str:
        task = self.tasks.get(task_id)
        if task is None:
            return f"no task {task_id!r}"
        if task.status is not Status.CLAIMED:
            return f"{task_id} is {task.status.value}, not claimed"
        if task.owner != by:
            return f"{task_id} is held by {task.owner}, not you"
        finished = replace(
            task,
            status=Status.FAILED if failed else Status.DONE,
            result=result.strip(),
            updated_at=_now(),
        )
        await self._put(finished)
        return finished

    async def get(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def list(self, status: Status | None = None) -> list[Task]:
        return [t for t in self.tasks.values() if status is None or t.status is status]

    async def _put(self, task: Task) -> None:
        self.tasks[task.task_id] = task
