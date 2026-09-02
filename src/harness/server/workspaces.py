"""A registered folder, and how one is identified.

The client discovers the folder locally -- it is the only side that can see where the person
was standing -- and asks for it to be bound. What is decided here is the identity: a
workspace id is a function of the path rather than something minted and stored, so it
survives a restart with nothing persisted, two clients registering the same folder get the
same id by construction rather than by a uniqueness constraint, and a thread's folder can be
recovered from the one durable fact about it -- the folder recorded in its session header.

`vcs` is declared by the client, never detected here. `repo_identity` is the one thing the
client asks the backend to record rather than to accept.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from hashlib import blake2s
from pathlib import Path
from typing import Any


class WorkspaceTaken(Exception):
    """This exact root is already registered.

    Not an error so much as a race: another client registered the same folder between one
    reading the list and writing to it, and the answer is to re-read rather than to fail.
    """


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    workspace_id: str
    name: str
    root_path: str
    vcs: str
    repo_identity: str = ""

    def wire(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "root_path": self.root_path,
            "vcs": self.vcs,
            "repo_identity": self.repo_identity,
        }


def workspace_id_for(root: Path) -> str:
    """A workspace id derived from its path rather than minted and stored.

    The id then survives a restart with no table behind it, and two clients registering the
    same folder get the same id by construction rather than by a uniqueness constraint. It
    also means a thread's workspace can be recovered from the only durable fact about it --
    the folder recorded in its session header.
    """
    return f"ws_{blake2s(str(root).encode(), digest_size=8).hexdigest()}"


@dataclass
class Workspaces:
    """The folders this process has been asked to work in.

    In memory. A client re-registers the folder it is standing in at boot and gets the same
    derived id back, so there is nothing here a restart loses that the next boot does not
    immediately restore.
    """

    known: dict[str, WorkspaceRecord] = field(default_factory=dict)

    def list(self) -> list[WorkspaceRecord]:
        return list(self.known.values())

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        return self.known.get(workspace_id)

    def for_root(self, root: Path) -> WorkspaceRecord | None:
        return self.known.get(workspace_id_for(root))

    async def register(
        self, name: str, root: Path, vcs: str, *, replace_existing: bool
    ) -> WorkspaceRecord:
        workspace_id = workspace_id_for(root)
        if workspace_id in self.known and not replace_existing:
            raise WorkspaceTaken(
                f"{root} is already registered. Re-read the list and use it."
            )
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            name=name or root.name or str(root),
            root_path=str(root),
            vcs="git" if vcs == "git" else "none",
            repo_identity=await repo_identity(root) if vcs == "git" else "",
        )
        self.known[workspace_id] = record
        return record

    def remember(self, root: Path) -> WorkspaceRecord:
        """Record a folder nobody registered explicitly.

        A thread loaded from the store names a folder that this process may never have been
        told about -- the registration lived in the previous process's memory. Recovering it
        from the session header is better than refusing to open a conversation whose
        transcript is right there.
        """
        record = self.known.get(workspace_id_for(root))
        if record is None:
            record = WorkspaceRecord(
                workspace_id=workspace_id_for(root),
                name=root.name or str(root),
                root_path=str(root),
                vcs="none",
            )
            self.known[record.workspace_id] = record
        return record


async def repo_identity(root: Path) -> str:
    """The checkout's root-commit set, or empty when it cannot be read.

    Recorded because a client that finds no identity here concludes the record may describe
    a different checkout than the one on disk and asks for a replacement -- every boot,
    forever. Computing it once is what lets that handshake settle. Any failure is empty
    rather than an error: a folder that is not a checkout, a machine with no `git`, and a
    repository with no commits are all ordinary, and none of them should stop a run.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "rev-list",
            "--max-parents=0",
            "HEAD",
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return ""
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except TimeoutError:
        process.kill()
        return ""
    if process.returncode != 0:
        return ""
    return ",".join(sorted(line.strip() for line in stdout.decode().split() if line.strip()))
