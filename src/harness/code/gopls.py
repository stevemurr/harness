"""Go, via gopls.

The whole of adding a second language, and the reason the interface exists. `gopls` is the
Go team's own server and effectively the only one, so there is no trade to weigh here --
which is itself worth noting, because Python's answer needed a paragraph and Go's needs a
sentence.

It wants a module: `gopls` resolves symbols through the package graph, so a folder with no
`go.mod` indexes as loose files and answers about very little. That is the backend's rule
rather than the harness's, and it surfaces the same way any other empty result does.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness.code.lsp import LspIndex


@dataclass
class Gopls(LspIndex):
    name: str = "gopls"
    command: tuple[str, ...] = ("gopls", "serve")
    extensions: tuple[str, ...] = (".go",)
    language_id: str = "go"
