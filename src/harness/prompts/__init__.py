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

## Where `system.md` came from

Its `# How you work` section is adapted from OpenAI's Codex CLI prompt
(`codex-rs/core/gpt_5_2_prompt.md`, Apache-2.0), which is the prompt their *general* models
get -- the codex-trained ones get a terse 80-line file instead, because the behaviour is
already in the weights. Ours is the general case, so the long one is the right comparison.

Adapted rather than copied: `apply_patch`, their approval-mode vocabulary and their inline
citation format name things this harness does not have, and instructing a model to use a tool
that does not exist is worse than saying nothing. The planning examples are theirs verbatim,
because teaching the shape by example is the whole technique and paraphrasing them would be
teaching a different shape.
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
