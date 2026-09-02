"""Swift, via sourcekit-lsp.

Apple's own server and effectively the only one, so -- as with `gopls` -- there is no trade
to weigh about which to use. The interesting parts of Swift are all about what the server
needs before it can answer.

## Where the binary is

`shutil.which` finds it, which is not obvious on macOS: `/usr/bin/sourcekit-lsp` is one of
Apple's `xcrun` shims, and the real executable lives inside the selected Xcode toolchain at
`.../XcodeDefault.xctoolchain/usr/bin/sourcekit-lsp`. The shim decides which toolchain that
is from `xcode-select`, at the moment it runs. So provisioning adopts the shim, the symlink
in the harness's bin folder points at the shim, and switching Xcode versions keeps working
without re-provisioning. On Linux the Swift toolchain puts the real binary on `PATH` and the
same lookup finds that instead.

There is no `install` recipe. Xcode cannot be installed from a subprocess, and a swift.org
toolchain is a large platform-specific download that has no business running inside a tool
call -- so a machine without Swift gets a sentence instead of a failed fetch, which is the
rule `Recipe` already states.

## It wants a build system, and then it wants a build

Two separate requirements, and missing either produces thin answers rather than an error --
so they are written down here, because "the tool returned nothing" is otherwise indistinguish-
able from "there is nothing called that".

**A build system.** sourcekit-lsp resolves a file's imports and module structure from one of
`Package.swift` (SwiftPM, understood natively), `compile_commands.json`, or a
`buildServer.json` pointing at a build server. An Xcode project is the common Swift case and
is *not* natively understood: it needs `xcode-build-server` to write that `buildServer.json`.
Without any of them the server falls back to treating files as loose, and cross-file
questions answer about very little.

**An index.** Unlike `gopls`, which reads the package graph directly, sourcekit-lsp answers
workspace-wide questions out of an index store that the *compiler* writes during a build
(`.build/index/store` for SwiftPM). A project that has been checked out but never built has
no store, so `find_definition` on a symbol in another file is thin until something has
compiled it once. `Symbols.warmup` covers a cold start, not an absent index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import override

from harness.symbols.base import Symbol
from harness.symbols.lsp import LspIndex, Recipe


@dataclass
class SourceKit(LspIndex):
    """Swift's two naming quirks, and nothing else different.

    A Swift method is indexed under its whole selector -- `balance(for:)`, `record(_:amount:)`
    -- and neither `balance` nor `Ledger.balance` matches that exactly. Measured against a
    built SwiftPM package: all three of those queries returned nothing while `balance(for:)`
    returned the method, which is a silence indistinguishable from "no such symbol" for the
    one shape a model is most likely to ask about.

    So a bare name matches the selector it heads, and the column search looks for the part
    that is actually written at the definition -- `func balance(for name: String)` contains
    `balance` and never contains `balance(for:)`, so without the second override every Swift
    method would be found and then refuse to have its references traced.
    """

    name: str = "sourcekit-lsp"
    extensions: tuple[str, ...] = (".swift",)
    language_id: str = "swift"
    recipe: Recipe = field(
        default_factory=lambda: Recipe(
            binary="sourcekit-lsp",
            doc=(
                "install Xcode (macOS) or a toolchain from swift.org, then make sure "
                + "`sourcekit-lsp` runs -- `xcrun --find sourcekit-lsp` shows where it is"
            ),
        )
    )

    @override
    def _same_symbol(self, offered: str, asked: str) -> bool:
        return offered == asked or offered.startswith(f"{asked}(")

    @override
    def _needle(self, symbol: Symbol) -> str:
        return symbol.name.split("(")[0]
