"""Run a named sequence of steps over some text."""

from pipeline.registry import lookup


def run_pipeline(text: str, names: list[str]) -> str:
    for name in names:
        text = lookup(name).apply(text)
    return text
