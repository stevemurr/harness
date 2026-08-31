"""What the model is told, as prose rather than as string literals.

The long-form prompts live here as Markdown because that is what they are: documents, edited
as writing, reviewed as writing, and diffed as writing. Finding a sentence to change used to
mean grepping Python for a fragment of it -- which is how `find_definition` kept a "prefer
grep" clause that argued against the tool it described, unnoticed across twenty eval runs.

**Tool descriptions deliberately stay with their tools.** Each is a sentence or two bound to
the JSON schema directly beneath it, and moving them would make adding a tool touch two
files -- against the one property `README.md` promises about adding one. The rule is whether
the prose stands alone: a system prompt does, a parameter's description does not.

Read through `importlib.resources` rather than by path, so it works from an installed wheel
and not only from a checkout.
"""

from __future__ import annotations

from importlib.resources import files


def prompt(name: str, **substitutions: str) -> str:
    """One prompt document, with `{placeholders}` filled in.

    Substituted by replacement rather than `str.format`, because these files are edited by
    hand and will eventually contain a JSON example or a shell brace. `format` would raise
    on the first one; replacement leaves anything it was not asked about alone.
    """
    text = (files(__package__) / f"{name}.md").read_text(encoding="utf-8")
    for key, value in substitutions.items():
        text = text.replace("{" + key + "}", value)
    return text
