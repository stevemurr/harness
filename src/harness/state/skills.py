"""Skills: instructions the model reads when they apply, kept beside the work.

A skill is a folder with a `SKILL.md` -- a name, a one-line description, and the
instructions -- and whatever scripts and references sit next to it. The model learns of
them in two stages, which is what keeps them cheap: the index, name and description only,
goes into the system prompt at the start of a run, and the body is read with `use_skill`
when one applies. A skill with ten pages of instructions costs one line of context until
the moment it is needed. A pinned skill is the exception: its body is in the prompt from
the start, for the one that always applies.

Two places are read, the folder's own `.harness/skills/` and the person's `~/.harness/
skills/`, and the folder wins a name clash: a project's version of "how we deploy" beats
the general one. A person invokes a skill by starting a message with `/name`; the model
reaches for one because the index told it to.

Read fresh each time rather than cached: a few small files, and a skill edited between two
runs -- or two turns -- is picked up without a restart. A malformed skill is skipped with a
warning and never stops a run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from harness.prompts import prompt
from harness.types import JSON

log = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"
#: Under the folder, beside the board and the other harness files a project keeps.
FOLDER_SKILLS = Path(".harness") / "skills"
#: The person's own, for every folder. The same home `config.py` uses; spelled here
#: rather than imported because `config` reads this package.
USER_SKILLS = Path("~/.harness/skills")
#: A name is a slash command and a folder name, so it is the intersection of what both
#: allow: lowercase, digits, hyphens, and short.
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: A body larger than this is a document, not an instruction, and would crowd out the
#: work. Read the file with `read_file` instead.
MAX_BODY = 40_000
_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.S)


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    body: str
    #: The skill's folder, so the model can read its references and run its scripts by
    #: path, through the ordinary tools and under the ordinary policy.
    path: Path
    #: In the system prompt from the start rather than read on demand.
    pinned: bool = False

    def wire(self) -> JSON:
        return {"name": self.name, "summary": self.description}


def load_skills(root: Path, *, user_home: Path = USER_SKILLS) -> tuple[Skill, ...]:
    """Every skill reachable from `root`, the folder's first, by name."""
    found: dict[str, Skill] = {}
    for base in (root / FOLDER_SKILLS, user_home.expanduser()):
        if not base.is_dir():
            continue
        for candidate in sorted(base.iterdir()):
            skill = read_skill(candidate)
            if skill is not None and skill.name not in found:
                found[skill.name] = skill
    return tuple(found.values())


def read_skill(folder: Path) -> Skill | None:
    """The skill in `folder`, or None with a warning for one that is not usable."""
    source = folder / SKILL_FILE
    if not source.is_file():
        return None
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("skill %s: %s", source, exc)
        return None
    fields, body = _split(text)
    name = fields.get("name") or folder.name
    if not _NAME.match(name):
        log.warning("skill %s: %r is not a usable name; skipped", source, name)
        return None
    body = body.strip()
    if not body:
        log.warning("skill %s: no instructions; skipped", source)
        return None
    if len(body) > MAX_BODY:
        log.warning("skill %s: %d characters is too long; skipped", source, len(body))
        return None
    description = fields.get("description") or body.splitlines()[0].lstrip("# ").strip()
    pinned = fields.get("pinned", "").strip().lower() in {"true", "yes", "1"}
    return Skill(name, description, body, folder, pinned)


def _split(text: str) -> tuple[dict[str, str], str]:
    """The frontmatter as `key: value` pairs, and the rest. A subset of YAML on purpose:
    one line per key, quotes optional, nothing nested -- a skill's header is three lines
    and a parser for the rest would be most of a dependency."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return {}, text
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            fields[key.strip().lower()] = value.strip().strip("\"'")
    return fields, text[match.end() :]


def skills_block(skills: tuple[Skill, ...]) -> str:
    """The system-prompt block: the index, and the pinned bodies. Empty with no skills."""
    if not skills:
        return ""
    index = "\n".join(f"- `{skill.name}`: {skill.description}" for skill in skills)
    pinned = "\n\n".join(
        f"## Skill: {skill.name}\n\n{skill.body}" for skill in skills if skill.pinned
    )
    return prompt("skills", index=index, pinned=pinned).rstrip() + "\n"


def expand(text: str, skills: tuple[Skill, ...]) -> str:
    """`/name the rest` as the model should read it: the skill's instructions, then what
    the person asked. Anything else is returned untouched, including a `/name` nobody
    defined -- the model may have been meant to see it."""
    head, _, rest = text.strip().partition(" ")
    if not head.startswith("/"):
        return text
    skill = next((s for s in skills if s.name == head[1:]), None)
    if skill is None:
        return text
    asked = rest.strip()
    return (
        f"Use the skill `{skill.name}` ({skill.path}). Its instructions:\n\n{skill.body}\n\n"
        + (f"The user's request: {asked}" if asked else "Carry it out now.")
    )


def write_skill(root: Path, name: str) -> Path | None:
    """A starter `SKILL.md` for `name` under the folder, or None if one is there.

    Written by a command and never on connect, for the reason `write_conventions` gives:
    a harness that writes into someone's repository unbidden is guessing and then quoting
    itself.
    """
    if not _NAME.match(name):
        raise ValueError(f"{name!r} is not a usable skill name: lowercase, digits, hyphens")
    target = root / FOLDER_SKILLS / name / SKILL_FILE
    if target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(prompt("skill_starter", skill=name), encoding="utf-8")
    return target


def skills_wire(root: Path) -> list[JSON]:
    """The index as a client lists it: the same shape as modes and policies."""
    return [skill.wire() for skill in load_skills(root)]
