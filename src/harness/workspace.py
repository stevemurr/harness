"""Turning a caller's path string into a real path, or refusing.

Every path a tool touches comes through here. Tools do not resolve their own paths, because
a tool that does is a tool that can leave the folder.

The rules below are short, and each one is here because its absence was a real bug in the
predecessor rather than because it sounded prudent:

  * Resolve first, compare second. A rule matched on the name the model typed is defeated
    by one symlink; only the resolved path can be compared to the root.
  * Join then normalise, never normalise then join. `normpath(".")` is `"."`, and joining
    that onto the root gives `/ws/.` -- a path that is not equal to the root, fails a
    containment test that uses equality, and reads as a subdirectory that does not exist.
  * A NUL byte reaches `Path.resolve()` as `ValueError` out of `lstat`. JSON permits it in
    a string, so a model can send one; unhandled it surfaces as "this harness is broken"
    rather than "that path is wrong".
  * Never follow a symlink at the final component of a write. Writing through a link
    writes wherever the link points, and the link is inside the folder while its target
    need not be.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


class WorkspaceError(Exception):
    """A path problem the model caused and can fix."""


class PathEscape(WorkspaceError):
    """Resolved outside the folder this run was given."""


class PathRefused(WorkspaceError):
    """Inside the folder, but not writable."""


@dataclass(frozen=True, slots=True)
class Workspace:
    """One directory tree, and what may be written inside it.

    `protected` are paths a run may not write even though they sit inside the folder. The
    set is deliberately tiny: the harness's own record of what the run did. A run that can
    rewrite that can rewrite the evidence of its own behaviour, which makes every other
    record unreliable. It is an integrity boundary, not a secrecy one -- reads are not
    restricted, because a coding agent that cannot read files in the folder it was pointed
    at is not useful, and the predecessor deleted its read floor for exactly that reason.
    """

    root: Path
    protected: tuple[Path, ...] = ()

    @classmethod
    def at(cls, root: Path | str, protected: tuple[Path, ...] = ()) -> Workspace:
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise WorkspaceError(f"workspace root is not a directory: {resolved}")
        return cls(resolved, tuple(p.resolve() for p in protected))

    def resolve(self, path: str) -> Path:
        """Resolve for reading. Containment only."""
        try:
            candidate = Path(path)
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (self.root / candidate).resolve()
            )
        except ValueError as exc:
            raise WorkspaceError(f"not a usable path: {path!r} ({exc})") from exc

        if resolved != self.root and not resolved.is_relative_to(self.root):
            raise PathEscape(
                f"path {path!r} resolves to {resolved}, outside {self.root}"
            )
        if resolved.is_symlink():
            # `resolve()` follows links, so an ordinary link is already its target here.
            # What survives is a cycle (a -> b -> a), where resolution gives up. `os.stat`
            # on one raises ELOOP, which would surface as a harness bug.
            raise PathEscape(f"path {path!r} is a symlink resolution cycle")
        return resolved

    def resolve_for_write(self, path: str) -> Path:
        """Resolve for writing. Containment AND write authority.

        Separate from `resolve` because they are two different questions, and a caller that
        asks only the first and then writes has asked half. In the predecessor that exact
        half-question let a tool delete the control journal.
        """
        resolved = self.resolve(path)
        for entry in self.protected:
            if resolved == entry or resolved.is_relative_to(entry):
                raise PathRefused(
                    f"refusing to write {path!r}: {entry} is this harness's own directory "
                    + "and is not writable from inside a run"
                )
        return resolved

    def write(self, path: str, content: str) -> int:
        """Write atomically, refusing a symlink at the final component.

        Write-then-rename so a crash cannot leave a half-file that the next read reports as
        a syntax error. The temporary name carries a nonce because two runs writing the
        same path in one tree would otherwise share a scratch file and each would rename
        the other's bytes into place.
        """
        target = self.resolve_for_write(path)
        if target.is_symlink():
            raise PathRefused(
                f"refusing to write {path!r}: it is a symbolic link. Name the file it "
                + "points at, or delete the link first."
            )
        data = content.encode("utf-8")
        tmp = target.with_name(f"{target.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
        return len(data)

    def read(self, path: str, limit: int = 400_000) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"not a file: {path}")
        data = target.read_bytes()[:limit]
        return data.decode("utf-8", errors="replace")

    def relative(self, target: Path) -> str:
        """A path as the model should see it: relative to the folder, POSIX separators."""
        try:
            return target.relative_to(self.root).as_posix() or "."
        except ValueError:
            return str(target)
