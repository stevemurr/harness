"""Rows of cells as an aligned text table."""

from __future__ import annotations


def is_number(cell: str) -> bool:
    try:
        float(cell)
    except ValueError:
        return False
    return True


def align(rows: list[list[str]]) -> str:
    """Columns padded to their widest cell: text flush left, numbers flush right, one
    space between columns, no trailing spaces."""
    if not rows:
        return ""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            if is_number(cell):
                cells.append(cell.rjust(widths[i]))
            else:
                cells.append(cell.rjust(widths[i]))
        lines.append(" ".join(cells).rstrip())
    return "\n".join(lines)
