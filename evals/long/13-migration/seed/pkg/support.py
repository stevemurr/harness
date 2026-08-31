"""Shared helpers. Not a stage."""

SEEN: list[tuple[str, int]] = []


def measure(stage: str, rows: int) -> None:
    SEEN.append((stage, rows))


def reset() -> None:
    SEEN.clear()
