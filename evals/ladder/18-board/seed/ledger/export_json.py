"""JSON export. In progress: see the board."""

from __future__ import annotations

from ledger.records import Record


def write_json(records: list[Record], path: str) -> None:
    raise NotImplementedError("JSON export is being written by another agent; see the board")
