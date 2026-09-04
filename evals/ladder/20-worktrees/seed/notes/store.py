"""The notes file: a JSON list of `{"id", "text", "tags"}`."""

from __future__ import annotations

import json
from pathlib import Path


def load(path: str | Path) -> list[dict]:
    file = Path(path)
    if not file.exists():
        return []
    return json.loads(file.read_text())


def save(path: str | Path, notes: list[dict]) -> None:
    Path(path).write_text(json.dumps(notes, indent=2) + "\n")


def add(path: str | Path, text: str) -> dict:
    notes = load(path)
    note = {"id": (max((n["id"] for n in notes), default=0) + 1), "text": text, "tags": []}
    notes.append(note)
    save(path, notes)
    return note
