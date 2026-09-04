"""`wkrender` as the harness reaches it: absent, adopted, and -- when it is installed on
this Mac -- real."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from harness.settings import Web as WebSettings
from harness.tools.browser import RenderFailed, RenderUnavailable, _Safari
from harness.tools.webkit import Rendered, WebKit, adopt


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


async def test_a_render_passes_what_it_was_asked_for(tmp_path: Path) -> None:
    """Viewport, dark, the script, the PNG path, the folder a file page may read, and the
    private-address guard all travel as flags; nothing else does."""
    fake = tmp_path / "wkrender"
    log = tmp_path / "argv.txt"
    _ = fake.write_text(
        "#!/bin/sh\n"
        + f"printf '%s\\n' \"$@\" > {log}\n"
        + 'printf \'%s\' \'{"url": "u", "title": "t", "html": "h", '
        + '"eval": {"n": 1}, "errors": ["e"], "failed": ["f"]}\'\n'
    )
    fake.chmod(0o755)
    page = await WebKit(path=str(fake), timeout=7).render(
        "file:///x/index.html",
        width=390,
        height=800,
        dark=True,
        reader=True,
        script="() => 1",
        png=tmp_path / "shot.png",
        full_page=True,
        files_under=tmp_path,
        block_private=True,
    )
    argv = log.read_text().splitlines()
    assert argv[:4] == ["--json", "--timeout", "7", "--viewport"] and argv[4] == "390x800"
    assert "--dark" in argv and "--reader" in argv and "--full-page" in argv
    assert "--block-private" in argv
    assert argv[argv.index("--eval") + 1] == "() => 1"
    assert argv[argv.index("--png") + 1] == str(tmp_path / "shot.png")
    assert argv[argv.index("--files-under") + 1] == str(tmp_path)
    assert argv[-1] == "file:///x/index.html"
    assert page.eval == {"n": 1} and page.errors == ("e",) and page.failed == ("f",)

    _ = await WebKit(path=str(fake)).render("https://example.com/")
    plain = log.read_text().splitlines()
    assert "--dark" not in plain and "--reader" not in plain and "--eval" not in plain
    assert "--png" not in plain


async def test_reader_mode_falls_back_once_for_an_older_binary(tmp_path: Path) -> None:
    """An installed pre-reader wkrender says only usage and exits 2 for the new flag."""
    fake = tmp_path / "wkrender"
    calls = tmp_path / "calls.txt"
    _ = fake.write_text(
        "#!/bin/sh\n"
        + f"printf x >> {calls}\n"
        + 'for arg in "$@"; do\n'
        + '  if [ "$arg" = "--reader" ]; then\n'
        + "    echo 'usage: wkrender [--json] URL' >&2\n"
        + "    exit 2\n"
        + "  fi\n"
        + "done\n"
        + 'printf \'%s\' \'{"url": "u", "title": "t", "html": "raw"}\'\n'
    )
    fake.chmod(0o755)

    page = await WebKit(path=str(fake)).render("https://example.com/", reader=True)

    assert page.html == "raw"
    assert calls.read_text() == "xx"


async def test_cancelling_a_render_kills_and_reaps_the_binary(tmp_path: Path) -> None:
    fake = tmp_path / "wkrender"
    pid_file = tmp_path / "pid.txt"
    _ = fake.write_text("#!/bin/sh\n" + f"printf '%s' $$ > {pid_file}\n" + "exec sleep 30\n")
    fake.chmod(0o755)
    task = asyncio.create_task(WebKit(path=str(fake)).render("https://example.com/"))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    pid = int(pid_file.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_page_reading_asks_for_reader_html_but_capture_keeps_the_live_page() -> None:
    class RecordingWebKit:
        def __init__(self) -> None:
            self.options: list[dict[str, object]] = []

        async def render(self, _url: str, **options: object) -> Rendered:
            self.options.append(options)
            return Rendered(url="u", title="t", html="reader", eval={})

    webkit = RecordingWebKit()
    safari = _Safari(WebSettings(), webkit)  # type: ignore[arg-type]

    assert (await safari.render("https://example.com/")).html == "reader"
    _ = await safari.capture("https://example.com/")

    assert webkit.options[0]["reader"] is True
    assert webkit.options[0]["block_private"] is True
    assert "reader" not in webkit.options[1]


async def test_a_binary_that_answers_is_read_as_json(tmp_path: Path) -> None:
    fake = tmp_path / "wkrender"
    _ = fake.write_text(
        "#!/bin/sh\n"
        + 'printf \'%s\' \'{"url": "https://example.com/final", "title": "T", '
        + '"html": "<p>hi</p>"}\'\n'
    )
    fake.chmod(0o755)
    page = await WebKit(path=str(fake)).render("https://example.com/")
    assert page.url == "https://example.com/final" and page.title == "T" and "hi" in page.html


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
async def test_output_overflow_stops_and_reaps_the_renderer(
    tmp_path: Path, channel: str
) -> None:
    fake = tmp_path / "wkrender"
    pid_file = tmp_path / "pid"
    code = (
        "import os, sys; from pathlib import Path; "
        + f"Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        + f"stream = sys.{channel}.buffer\n"
        + "while True: stream.write(b'x' * 65536); stream.flush()\n"
    )
    fake.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(code)}\n")
    fake.chmod(0o755)
    async with asyncio.timeout(3):
        with pytest.raises(RenderFailed, match=f"{channel} exceeded"):
            await WebKit(path=str(fake), max_output_bytes=1000, max_error_bytes=1000).render(
                "https://example.com/"
            )
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


async def test_fractional_render_timeout_is_an_overall_budget(tmp_path: Path) -> None:
    fake = tmp_path / "wkrender"
    fake.write_text("#!/bin/sh\nexec sleep 30\n")
    fake.chmod(0o755)
    async with asyncio.timeout(1):
        with pytest.raises(RenderFailed, match="within 0.05s"):
            await WebKit(path=str(fake), timeout=0.05).render("https://example.com/")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"html": 3},
        {"url": "u", "title": "t", "html": ""},
        {"url": "u", "title": "t", "html": "h", "errors": "oops"},
    ],
)
async def test_malformed_renderer_json_does_not_become_a_page(
    tmp_path: Path, payload: object
) -> None:
    fake = tmp_path / "wkrender"
    fake.write_text("#!/bin/sh\nprintf '%s' " + shlex.quote(json.dumps(payload)) + "\n")
    fake.chmod(0o755)
    with pytest.raises(RenderFailed, match="invalid"):
        await WebKit(path=str(fake)).render("https://example.com/")


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


def test_adopting_the_installed_binary_itself_does_not_delete_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--from ~/.harness/bin`, or that folder on `PATH`: the candidate is the target,
    and unlinking the target first left nothing to copy."""
    import harness.tools.webkit as module

    monkeypatch.setattr(module, "BIN", tmp_path / "bin")
    installed = tmp_path / "bin" / "wkrender"
    installed.parent.mkdir()
    _ = installed.write_text("#!/bin/sh\necho installed\n")
    installed.chmod(0o755)

    assert adopt(installed) == installed
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert adopt() == installed
    assert installed.read_text() == "#!/bin/sh\necho installed\n"


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
