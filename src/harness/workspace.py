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
from difflib import SequenceMatcher
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
    #: Other folders this run may reach, for an editor whose project is several. The
    #: `root` stays the one folder: relative paths resolve against it, commands run in
    #: it, and its files are shown relative; a file in another folder is named by its
    #: absolute path, which is the only name that says which folder it is in.
    extra: tuple[Path, ...] = ()

    @classmethod
    def at(
        cls,
        root: Path | str,
        protected: tuple[Path, ...] = (),
        extra: tuple[Path | str, ...] = (),
    ) -> Workspace:
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise WorkspaceError(f"workspace root is not a directory: {resolved}")
        others: list[Path] = []
        for folder in extra:
            other = Path(folder).resolve()
            if not other.is_dir():
                raise WorkspaceError(f"workspace folder is not a directory: {other}")
            if other != resolved and other not in others:
                others.append(other)
        return cls(resolved, tuple(p.resolve() for p in protected), tuple(others))

    @property
    def roots(self) -> tuple[Path, ...]:
        """Every folder this run may reach, the root first."""
        return (self.root, *self.extra)

    def _inside(self, resolved: Path) -> bool:
        return any(resolved == r or resolved.is_relative_to(r) for r in self.roots)

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

        if not self._inside(resolved):
            where = self.root if not self.extra else ", ".join(str(r) for r in self.roots)
            hint = self.near_miss(resolved)
            raise PathEscape(
                f"path {path!r} resolves to {resolved}, outside {where}"
                + (f". {hint}" if hint else "")
            )
        if resolved.is_symlink():
            # `resolve()` follows links, so an ordinary link is already its target here.
            # What survives is a cycle (a -> b -> a), where resolution gives up. `os.stat`
            # on one raises ELOOP, which would surface as a harness bug.
            raise PathEscape(f"path {path!r} is a symlink resolution cycle")
        return resolved

    def near_miss(self, path: Path | str) -> str:
        """A sentence naming the misspelling, when `path` does not exist and is one
        folder name away from one of this workspace's roots -- or an empty string.

        Measured twice, on 2026-09-01 and 2026-09-03: a model retyping the absolute path
        of the working folder into every command got one character of it wrong --
        `stevemurm` for `stevemurr` -- and then made the same call 13 times, because the
        answer it got ("outside the folder", "No such file or directory") never said what
        was wrong with the path. This says it: the folder name that differs, and what it
        should be. Only when the path does not exist, so a real second folder that
        happens to have a similar name is never called a typo.
        """
        candidate = Path(path)
        if not candidate.is_absolute() or candidate.exists():
            return ""
        parts = candidate.parts
        for root in self.roots:
            expected = root.parts
            compared = min(len(parts), len(expected))
            if compared < 2:
                continue
            differing = [
                index
                for index in range(compared)
                if parts[index] != expected[index]
            ]
            if len(differing) != 1:
                continue
            index = differing[0]
            wrong, right = parts[index], expected[index]
            if SequenceMatcher(None, wrong, right).ratio() < 0.75:
                continue
            return (
                f"{candidate} does not exist, and looks like a misspelling of the working "
                + f"folder {root}: {wrong!r} should be {right!r}. Use relative paths, which "
                + "resolve against the working folder, rather than retyping it."
            )
        return ""

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
            _ = tmp.write_bytes(data)
            _ = tmp.replace(target)
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
