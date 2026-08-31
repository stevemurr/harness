"""Python, via basedpyright.

**Why basedpyright.** It is the mainstream open Python server today -- Zed's default since
v0.204.0, and the closest open thing to Pylance feature-for-feature. What most Python
developers actually run is Pylance, which is licence-restricted to official VS Code builds
and so is unavailable to anything else. `pylsp` has faded from current recommendations.
Astral's `ty` is faster and built for this from the ground up, and is still beta; when it
reaches 1.0 it is one line of `[code.commands]` away, with no code change at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness.code.lsp import LspIndex


@dataclass
class Pyright(LspIndex):
    name: str = "basedpyright"
    command: tuple[str, ...] = ("basedpyright-langserver", "--stdio")
    extensions: tuple[str, ...] = (".py", ".pyi")
    language_id: str = "python"
