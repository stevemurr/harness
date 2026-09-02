"""The tools, and the two contracts around them.

`Tool` is what an author writes, `Handler` is that tool as the harness dispatches it and
`bind` is the step between; `Registry` holds handlers, and `new_registry` makes one. Both
live here because they are the tool package's
to define: the agent consumes a registry, it does not know how to build one.

`Toolkit`, the set of tools a coding agent gets and the state they share, is in `kit.py`.
It is imported by name rather than re-exported, so importing this package does not
import every tool.
"""

from harness.tools.base import (
    Arguments,
    Handler,
    Minimum,
    MinItems,
    MinLength,
    Previews,
    Registry,
    Tool,
    ToolContext,
    bind,
    described,
    new_registry,
    spec_for,
)
from harness.types import JSON

__all__ = [
    "JSON",
    "Arguments",
    "Handler",
    "MinItems",
    "MinLength",
    "Minimum",
    "Previews",
    "Registry",
    "Tool",
    "ToolContext",
    "bind",
    "described",
    "new_registry",
    "spec_for",
]
