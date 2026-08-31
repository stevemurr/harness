"""Telling the model where it is.

Both reference implementations do this by injecting a block into the system prompt at
startup, and neither offers an environment query tool. That is the right call and the reason
is not taste: the information is small, needed on the first turn, and a tool means the model
has to *think to ask* -- which is exactly what a model that does not know where it is will
fail to do.

The evidence is measured. Across four live scenarios on 2026-08-30 the model twice tried to
write to `/home/user`, once with `write_file` and once with `cd`. Containment refused both,
correctly, but the system prompt said the word "folder" twelve times and never once named
it. And a test the model wrote hardcoded `python`, which on that machine is Python 2.7.18 --
a fact it had no way to know and no reason to suspect.

**Facts here, preferences in a file.** Everything below is observed: a path, a version, what
is on disk. How *this* project likes to be worked on -- use uv, run the linter before
committing, never touch the generated directory -- is a preference, and a harness that
hardcoded Python opinions would be wrong for the next language and stale for this one. Those
live in AGENTS.md, which is read and appended verbatim, the way Claude Code reads CLAUDE.md
and Codex reads AGENTS.md.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

#: Read from the workspace root and appended to the prompt verbatim. The first name is the
#: one this harness documents; the other two are read because a repository that already has
#: one should not need a third.
CONVENTION_FILES = ("AGENTS.md", "CLAUDE.md", ".harness.md")

#: Files whose presence says what the project is built with, so the model does not guess.
#: Facts, not instructions -- `uv.lock` present is a fact; "use uv" is a preference, and
#: belongs in AGENTS.md.
MARKERS = (
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "justfile",
)


def describe(root: Path, *, limit: int = 40) -> str:
    """The environment block, as the model will read it."""
    lines = [
        "# Environment",
        "",
        f"Working folder: {root}",
        "Relative paths in every tool resolve there. It is not a sandbox: `run` executes "
        "with the user's own authority, so name paths inside the folder unless you have "
        "said why you need to leave it.",
        "",
        f"Platform: {platform.system().lower()} {platform.release()}",
        f"Python (this harness): {sys.version.split()[0]}",
    ]

    if note := _python_note():
        lines.append(note)

    if branch := _git_branch(root):
        lines.append(f"Git: yes, on {branch}")
    else:
        lines.append("Git: not a repository")

    if found := [m for m in MARKERS if (root / m).exists()]:
        lines.append(f"Project files present: {', '.join(found)}")

    entries = sorted(
        (f"{p.name}/" if p.is_dir() else p.name)
        for p in root.iterdir()
        if not p.name.startswith(".")
    )
    if entries:
        shown = ", ".join(entries[:limit])
        more = f" (+{len(entries) - limit} more)" if len(entries) > limit else ""
        lines.append(f"Contents: {shown}{more}")
    else:
        lines.append("Contents: the folder is empty")

    if conventions := _conventions(root):
        lines += ["", conventions]

    return "\n".join(lines) + "\n"


def _python_note() -> str:
    """What bare `python` actually is, when it is not what a model would assume.

    A model writing `subprocess.run(["python", ...])` in a test is making an assumption it
    cannot check, and on a machine where `python` is 2.7 the test fails everywhere except
    the venv it was written in. Observed exactly that on 2026-08-30.
    """
    binary = shutil.which("python")
    if binary is None:
        return "`python` is not on PATH. Use `python3`, or `sys.executable` from inside Python."
    try:
        out = subprocess.run(
            [binary, "-c", "import sys; print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    version = out.stdout.strip()
    if version.startswith("3."):
        return f"`python` on PATH is {version}."
    return (
        f"`python` on PATH is {version or 'not Python 3'} -- NOT Python 3. Use `python3` in "
        "shell commands, and `sys.executable` when a Python program spawns another."
    )


def _git_branch(root: Path) -> str:
    if not (root / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() or ""


def _conventions(root: Path) -> str:
    """The project's own instructions, verbatim.

    Verbatim and unparsed on purpose: this is the user's file, and a harness that summarised
    it would be deciding which of their instructions mattered.
    """
    for name in CONVENTION_FILES:
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return f"# {name}\n\n{text}"
    return ""
