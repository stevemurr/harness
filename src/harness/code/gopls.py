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

from dataclasses import dataclass, field

from harness.code.lsp import LspIndex, Recipe


@dataclass
class Gopls(LspIndex):
    name: str = "gopls"
    arguments: tuple[str, ...] = ("serve",)
    extensions: tuple[str, ...] = (".go",)
    language_id: str = "go"
    recipe: Recipe = field(
        default_factory=lambda: Recipe(
            binary="gopls",
            # Needs a Go toolchain, and `go install` puts it in GOBIN rather than anywhere
            # this can predict -- so provisioning finds it and links it. Nobody writing Go
            # lacks a toolchain, which is why no download is offered here.
            install=("go", "install", "golang.org/x/tools/gopls@latest"),
            doc="go install golang.org/x/tools/gopls@latest",
        )
    )
