"""The language servers this harness manages, and where they live.

    ~/.harness/servers/bin/
        basedpyright-langserver   -> a symlink, or an installed binary
        gopls                     -> a symlink to the one already on PATH

**One place to look.** `LspIndex.argv` consults this folder and nothing else. `PATH` is a
property of whoever's shell started the process: a developer terminal has `gopls`, a server
started at boot by a supervisor does not, and neither knows which version it found.
Adopting a binary into this folder turns "happens to be installed" into "was chosen", and
makes the absent case a sentence a person can act on rather than a guess about their
environment.

**Symlink before download.** A Go developer already has `gopls` and does not want a second
copy; provisioning finds it and links it. Only when nothing is there does a recipe run, and
only from `harness --install-servers` -- never during a run, where a 272MB fetch would blow
the request timeout and fail somewhere a model cannot understand.

**Nothing here knows a language.** Each server's binary, install command and fallback
sentence live on its own file as a `Recipe`, so adding a language stays one file. This
module knows only the shape of a recipe and the name of a folder.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from harness.settings import Symbols
from harness.symbols.base import Indexes, servers_bin
from harness.symbols.lsp import LspIndex

log = logging.getLogger(__name__)

def known() -> tuple[type[LspIndex], ...]:
    """Every language the harness can index.

    The one list, and the one line adding a language adds outside its own file -- the same
    shape as `Toolkit.tools` naming its tools. Imported here rather than at module scope
    so `settings` and `base` stay free of language imports.
    """
    from harness.symbols.gopls import Gopls
    from harness.symbols.pyright import Pyright
    from harness.symbols.sourcekit import SourceKit

    return (Pyright, Gopls, SourceKit)


@dataclass(frozen=True, slots=True)
class Outcome:
    """What provisioning did about one server, in words a person reads."""

    name: str
    detail: str
    ready: bool


def for_workspace(root: Path, settings: Symbols | None = None) -> Indexes:
    """The indexes worth having over one folder.

    Only languages the folder actually contains. A repository is almost always polyglot in
    the trivial sense -- Markdown, TOML, a shell script beside the code -- and registering a
    server for every language the harness knows would start a Go process to answer a
    question about a Python file. So the extensions present decide, which needs no
    per-language code: a backend already declares what it speaks for.

    Constructing an index starts nothing. The process waits for the first query, so a
    registered-but-unused language costs one object.
    """
    settings = settings or Symbols()
    if not settings.enabled:
        return Indexes()
    present = _extensions_in(root)
    return Indexes(
        [factory(root, settings) for factory in known() if present & _speaks(factory)]
    )


def _speaks(factory: type[LspIndex]) -> set[str]:
    """What a backend claims, read from the class rather than an instance.

    Constructing one to ask would need a root, and the question is asked while deciding
    whether a root has any use for it. A dataclass field's default is the class attribute,
    so the class answers.
    """
    return set(factory.extensions)


def _extensions_in(root: Path, limit: int = 20_000) -> set[str]:
    """Which file extensions this folder holds, cheaply and boundedly.

    Walked rather than globbed per language, so the cost is one pass whatever the harness
    knows about. Skips the directories `files.py` already skips, because a `.go` file inside
    `node_modules` is not this project's language.
    """
    from harness.tools.files import SKIP

    found: set[str] = set()
    seen = 0
    for _current, directories, names in os.walk(root):
        directories[:] = [d for d in directories if d not in SKIP and not d.startswith(".")]
        for name in names:
            suffix = Path(name).suffix.lower()
            if suffix:
                found.add(suffix)
            seen += 1
            if seen >= limit:
                return found
    return found


async def provision(settings: Symbols | None = None) -> list[Outcome]:
    """Set up every known server, and say what happened to each.

    Order matters and is the whole design: already provisioned, then adopt from `PATH`, then
    run the recipe, then explain. Each step is cheaper and less surprising than the next.
    """
    settings = settings or Symbols()
    target = servers_bin()
    target.mkdir(parents=True, exist_ok=True)
    outcomes: list[Outcome] = []

    for factory in known():
        index = factory(Path.cwd(), settings)
        recipe = index.recipe
        link = target / recipe.binary

        if link.exists():
            outcomes.append(Outcome(index.name, f"already at {link}", True))
            continue

        found = shutil.which(recipe.binary)
        if found:
            outcomes.append(Outcome(index.name, _adopt(Path(found), link), True))
            continue

        if not recipe.install:
            outcomes.append(Outcome(index.name, f"not found. Install: {recipe.doc}", False))
            continue

        detail, ready = await _run(recipe.install)
        if ready:
            found = shutil.which(recipe.binary)
            if found:
                outcomes.append(Outcome(index.name, _adopt(Path(found), link), True))
                continue
            detail = f"installed, but {recipe.binary} is still not on PATH"
        outcomes.append(Outcome(index.name, f"{detail}. Install: {recipe.doc}", False))

    return outcomes


def _adopt(found: Path, link: Path) -> str:
    """Link what is already here, rather than fetching a second copy."""
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(found)
    except OSError as exc:
        return f"found at {found}, but could not link it: {exc}"
    return f"linked {link.name} -> {found}"


async def _run(command: tuple[str, ...]) -> tuple[str, bool]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, PermissionError) as exc:
        return f"cannot run {command[0]}: {exc}", False
    output, _ = await process.communicate()
    if process.returncode == 0:
        return f"ran {' '.join(command)}", True
    tail = output.decode("utf-8", "replace").strip().splitlines()[-1:]
    return f"{' '.join(command)} exited {process.returncode}: {tail[0] if tail else ''}", False
