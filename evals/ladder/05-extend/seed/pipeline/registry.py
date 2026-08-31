"""Which name means which step."""

from pipeline.steps import Squeeze, Strip, Upper

STEPS = {"upper": Upper(), "strip": Strip(), "squeeze": Squeeze()}


def lookup(name: str):
    if name not in STEPS:
        raise KeyError(f"no step named {name!r}. Known: {', '.join(sorted(STEPS))}")
    return STEPS[name]
