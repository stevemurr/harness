"""`wkrender` as the harness reaches it: absent, adopted, and -- when it is installed on
this Mac -- real."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.tools.browser import RenderFailed, RenderUnavailable
from harness.tools.webkit import WebKit, adopt


async def test_without_the_binary_the_answer_names_the_install_command(tmp_path: Path) -> None:
    webkit = WebKit(path=str(tmp_path / "nope"))
    assert not webkit.available
    with pytest.raises(RenderUnavailable, match="install-webkit"):
        _ = await webkit.render("https://example.com/")


async def test_a_binary_that_fails_says_what_it_said(tmp_path: Path) -> None:
    fake = tmp_path / "wkrender"
    _ = fake.write_text("#!/bin/sh\necho 'wkrender: navigation failed: no route' >&2\nexit 1\n")
    fake.chmod(0o755)
    webkit = WebKit(path=str(fake))
    assert webkit.available
    with pytest.raises(RenderFailed, match="no route"):
        _ = await webkit.render("https://example.com/")


async def test_a_binary_that_answers_is_read_as_json(tmp_path: Path) -> None:
    fake = tmp_path / "wkrender"
    _ = fake.write_text(
        "#!/bin/sh\n"
        + "printf '%s' '{\"url\": \"https://example.com/final\", \"title\": \"T\", "
        + "\"html\": \"<p>hi</p>\"}'\n"
    )
    fake.chmod(0o755)
    page = await WebKit(path=str(fake)).render("https://example.com/")
    assert page.url == "https://example.com/final" and page.title == "T" and "hi" in page.html


def test_adopt_copies_a_built_binary_into_the_harness_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import harness.tools.webkit as module

    monkeypatch.setattr(module, "BIN", tmp_path / "bin")
    checkout = tmp_path / "wkrender"
    built = checkout / ".build" / "release" / "wkrender"
    built.parent.mkdir(parents=True)
    _ = built.write_text("#!/bin/sh\necho built\n")

    installed = adopt(checkout)

    assert installed == tmp_path / "bin" / "wkrender"
    assert installed.read_text() == "#!/bin/sh\necho built\n"
    assert installed.stat().st_mode & 0o111
    with pytest.raises(FileNotFoundError, match="build it first"):
        _ = adopt(tmp_path / "elsewhere")


async def test_the_real_binary_renders_a_local_page_as_safari(tmp_path: Path) -> None:
    """Runs only where `harness install-webkit` has been run. A page built by script,
    and the user agent WebKit builds for itself: Safari's, not a hand-written one."""
    webkit = WebKit()
    if not webkit.available:
        pytest.skip("wkrender is not installed")
    page_file = tmp_path / "page.html"
    _ = page_file.write_text(
        "<!doctype html><html><head><title>Local</title></head><body><div id='app'></div>"
        + "<script>document.getElementById('app').textContent = 'ua:' + navigator.userAgent;"
        + "</script></body></html>"
    )

    page = await webkit.render(page_file.as_uri())

    assert page.title == "Local"
    assert "AppleWebKit/605.1.15" in page.html and "Safari/605.1.15" in page.html
    assert "Version/" in page.html and "Chrome" not in page.html
