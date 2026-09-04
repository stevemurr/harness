"""Turn a title into a URL slug."""

from __future__ import annotations

import re
import unicodedata


def slug(text: str) -> str:
    """Lowercase ASCII, words joined by single hyphens, nothing on the ends."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    lowered = ascii_text.lower()
    hyphenated = re.sub(r"[^a-z0-9]", "-", lowered)
    return hyphenated.strip("-")
