"""What the watch page actually renders.

`shared.js` is hand-written and inlined into a page at request time -- no module boundary and
no build step, both deliberate (see `server.page`), and both reasons nothing else in this
suite can see it. So these run the renderer through `node` and assert on the shape it builds.

The bug that earned this file was visible only on a screen. The model wrote **`roman.py`**
in its summary and the page showed the backticks, because `strong` set its text directly and
never looked inside it -- and the same leaf took italics and links nested in bold with it.
Nothing failed, nothing logged; the page just quietly showed source instead of prose.

Skipped where node is absent. The pages are not part of the harness's runtime, so a machine
without node can still run everything that is.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "src" / "harness" / "server" / "pages" / "shared.js"
RENDER = Path(__file__).resolve().parent / "render.mjs"

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def render(source: str) -> str:
    done = subprocess.run(
        ["node", str(RENDER), str(SHARED), source],
        capture_output=True, text=True, timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_code_inside_bold_is_code_and_not_backticks() -> None:
    """The one that was on screen. `put` assigned textContent, so bold was a leaf."""
    out = render("**`roman.py`** is the file")

    assert "<strong><code>roman.py</code></strong>" in out
    assert "`" not in out


def test_emphasis_and_links_nest_inside_bold_too() -> None:
    """Not a backtick bug. Everything nested in bold came out as its own source."""
    nested = render("**bold with *em* inside**")

    assert "<strong>bold with <em>em</em> inside</strong>" in nested
    assert "<strong><a>a link</a></strong>" in render("**[a link](https://x.test)**")


def test_the_rest_of_the_line_survives_the_nesting() -> None:
    """The trap the fix had to avoid, and the reason `inline` builds its own regex.

    `INLINE` is a module-level `/g` pattern and `lastIndex` lives on the object. Recursing
    through the shared one resets the position the *outer* loop is part-way through, so the
    remainder of the line is dropped -- a worse bug than the one being fixed, and a quieter
    one. This is the assertion that catches it: everything after the bold must still be here.
    """
    out = render("- **`roman.py`** -- `to_roman(n)` and `from_roman(s)` ok.")

    assert "<strong><code>roman.py</code></strong>" in out
    assert "<code>to_roman(n)</code>" in out
    assert "<code>from_roman(s)</code>" in out
    assert out.rstrip().endswith("ok.</li></ul>")


def test_a_code_span_stays_literal() -> None:
    """Code is a leaf on purpose: what is inside it is text, whatever it looks like."""
    out = render("`**not bold** inside code`")

    assert "<code>**not bold** inside code</code>" in out
    assert "<strong>" not in out


def test_snake_case_is_not_italicised() -> None:
    """`_em_` and `__bold__` are unsupported deliberately, and recursion must not revive
    them: this is a page for reading about code, where `a_b_c` is a name."""
    assert "<em>" not in render("call some_helper_name(x) twice")
