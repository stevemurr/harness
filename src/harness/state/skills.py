"""Skills: instructions the model reads when they apply, kept beside the work.

A skill is a folder with a `SKILL.md` -- a name, a one-line description, and the
instructions -- and whatever scripts and references sit next to it. The model learns of
them in two stages, which is what keeps them cheap: the index, name and description only,
goes into the system prompt at the start of a run, and the body is read with `use_skill`
when one applies. A skill with ten pages of instructions costs one line of context until
the moment it is needed. A pinned skill is the exception: its body is in the prompt from
the start, for the one that always applies.

Three places are read -- the folder's own `.harness/skills/`, the person's `~/.harness/
skills/`, and the ones this package ships -- and the nearer wins a name clash: a project's
version of "how we debug" beats the person's, which beats the built-in one. A person
invokes a skill by starting a message with `/name`; the model reaches for one because the
index told it to; and a skill may name `triggers`, words that make the harness point the
model at it before the first turn, because a model that only reads the index will
sometimes not look. A skill may also list `steps`, a workflow: using it seeds the run's
checklist with them, so where the work stands is visible in the plan a client already
shows, and nothing new has to hold that state.

Read fresh each time rather than cached: a few small files, and a skill edited between two
runs -- or two turns -- is picked up without a restart. A malformed skill is skipped with a
warning and never stops a run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib.resources import files
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
#: The skills this package ships: debugging, testing, architecture, design. The furthest
#: source, so a folder or a person can replace any of them by name.
BUILTIN_SKILLS = Path(str(files("harness.skills")))
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
    #: Words or phrases in a request that mean this skill applies. Matched whole, case
    #: aside, by the harness, which then tells the model to read the skill first.
    triggers: tuple[str, ...] = ()
    #: The workflow, when the skill is one: the steps a run takes, seeded into its
    #: checklist when the skill is used.
    steps: tuple[str, ...] = ()

    def wire(self) -> JSON:
        return {"name": self.name, "summary": self.description}


def load_skills(
    root: Path, *, user_home: Path = USER_SKILLS, builtin: Path = BUILTIN_SKILLS
) -> tuple[Skill, ...]:
    """Every skill reachable from `root`: the folder's, then the person's, then the
    built-in ones, the nearer winning a name."""
    found: dict[str, Skill] = {}
    for base in (root / FOLDER_SKILLS, user_home.expanduser(), builtin):
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
    name = _text(fields, "name") or folder.name
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
    description = _text(fields, "description") or body.splitlines()[0].lstrip("# ").strip()
    pinned = _text(fields, "pinned").lower() in {"true", "yes", "1"}
    return Skill(
        name,
        description,
        body,
        folder,
        pinned,
        triggers=tuple(t.lower() for t in _list(fields, "triggers")),
        steps=_list(fields, "steps"),
    )


def _text(fields: dict[str, str | list[str]], key: str) -> str:
    value = fields.get(key, "")
    return value.strip() if isinstance(value, str) else ", ".join(value)


def _list(fields: dict[str, str | list[str]], key: str) -> tuple[str, ...]:
    value = fields.get(key, ())
    if isinstance(value, str):
        value = [value] if value else []
    return tuple(item for item in (v.strip() for v in value) if item)


def _split(text: str) -> tuple[dict[str, str | list[str]], str]:
    """The frontmatter as `key: value` pairs and `key:` lists, and the rest.

    A subset of YAML on purpose: one line per key, quotes optional, a list either inline
    as `[a, b]` or as `- item` lines under a bare key, nothing nested. A skill's header
    is a handful of lines and a parser for the rest would be most of a dependency.
    """
    match = _FRONTMATTER.match(text)
    if match is None:
        return {}, text
    fields: dict[str, str | list[str]] = {}
    current: str | None = None
    for line in match.group(1).splitlines():
        if current is not None and line.lstrip().startswith("- "):
            items = fields[current]
            if isinstance(items, list):
                items.append(line.lstrip()[2:].strip().strip("\"'"))
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            continue
        key, value = key.strip().lower(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            fields[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
            current = None
        elif value:
            fields[key] = value.strip("\"'")
            current = None
        else:
            fields[key] = []
            current = key
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


def invoked(text: str, skills: tuple[Skill, ...]) -> Skill | None:
    """The skill a `/name …` message names, or None."""
    head, _, _ = text.strip().partition(" ")
    if not head.startswith("/"):
        return None
    return next((s for s in skills if s.name == head[1:]), None)


def expand(text: str, skills: tuple[Skill, ...]) -> str:
    """`/name the rest` as the model should read it: the skill's instructions, then what
    the person asked. Anything else is returned untouched, including a `/name` nobody
    defined -- the model may have been meant to see it."""
    skill = invoked(text, skills)
    if skill is None:
        return text
    asked = text.strip().partition(" ")[2].strip()
    return (
        f"Use the skill `{skill.name}` ({skill.path}). Its instructions:\n\n{skill.body}\n\n"
        + steps_note(skill)
        + (f"The user's request: {asked}" if asked else "Carry it out now.")
    )


def steps_note(skill: Skill) -> str:
    """The workflow's steps as the model is told them, or nothing for a skill without."""
    if not skill.steps:
        return ""
    listed = "\n".join(f"{i}. {step}" for i, step in enumerate(skill.steps, 1))
    return (
        f"Its steps, in order:\n\n{listed}\n\nRecord them with update_plan before you start "
        + "and keep it current as you go.\n\n"
    )


def trigger(text: str, skills: tuple[Skill, ...]) -> Skill | None:
    """The first skill whose trigger the request names, whole and case aside, or None.

    A pointer, not a decision: the harness tells the model the skill applies and the
    model reads it, so a person's `/name` -- which says which skill outright -- is left
    to `expand`, and a request naming no trigger gets the index alone.
    """
    if text.lstrip().startswith("/"):
        return None
    lowered = f" {re.sub(r'[^a-z0-9]+', ' ', text.lower())} "
    for skill in skills:
        if any(f" {word} " in lowered for word in skill.triggers):
            return skill
    return None


def trigger_note(skill: Skill) -> str:
    """What the harness says when a trigger matched: which skill, and what to do first."""
    return (
        f"The `{skill.name}` skill applies to this request: {skill.description} "
        + f'Call use_skill("{skill.name}") and follow it before doing anything else.'
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
