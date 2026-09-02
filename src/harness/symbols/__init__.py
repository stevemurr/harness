"""What a symbol is, where it is defined, and where it is used.

`Symbol` and `Location` are the vocabulary; `SymbolIndex` is the contract one language
satisfies over one folder; `Indexes` is every language a folder has, asked together. The
only implementations today speak LSP (`lsp.py`, and a file per language beside it), and the
test suite holds a second one that does not -- which is why the package is named for the
question it answers and not for the wire the servers happen to use.
"""

from harness.symbols.base import (
    Indexes,
    Location,
    Symbol,
    SymbolIndex,
    SymbolIndexError,
    servers_bin,
)

__all__ = ["Indexes", "Location", "Symbol", "SymbolIndex", "SymbolIndexError", "servers_bin"]
