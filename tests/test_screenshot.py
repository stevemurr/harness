"""The screenshot tool: a file for a person, a reading for the model, and the rules for
what a page may load. Driven against a fake renderer, and once against the real browser
when it is installed."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from harness.settings import Web as WebSettings
from harness.tools.base import ToolContext, bind
from harness.tools.browser import (
    Capture,
    RenderFailed,
    RenderUnavailable,
    file_error,
    reading_of,
)
from harness.tools.kit import Toolkit
from harness.tools.screenshot import Screenshot, screenshot_tools
from harness.workspace import Workspace

ANYWHERE = replace(WebSettings(), block_private=False)
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def _capture(url: str = "file:///tmp/index.html", **fields: object) -> Capture:
    base = Capture(
        png=PNG,
        url=url,
        title="Ada Byron",
        viewport=(390, 800),
        document=(390, 2200),
        headings=("h1: Ada Byron", "h2: Work"),
        landmarks="header=1 nav=1 main=1 footer=1 aside=0 section=3 article=0 form=1",
        links=7,
        images=2,
        images_without_alt=1,
        font="Inter, sans-serif",
        font_size="16px",
        color="rgb(20, 20, 20)",
        background="rgb(250, 250, 250)",
        text_chars=1200,
    )
    return replace(base, **fields)  # type: ignore[arg-type]


class _FakeRenderer:
    def __init__(self, shot: Capture | None = None, error: Exception | None = None) -> None:
        self.shot, self.error = shot, error
        self.captured: list[tuple[str, dict[str, object]]] = []

    async def render(self, url: str) -> str:
        return ""

    async def capture(self, url: str, **options: object) -> Capture:
        self.captured.append((url, options))
        if self.error is not None:
            raise self.error
        assert self.shot is not None
        return replace(self.shot, url=url)

    async def aclose(self) -> None:
        return None


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    _ = (tmp_path / "index.html").write_text("<!doctype html><title>Ada</title><h1>Ada</h1>")
    return Workspace.at(tmp_path)


async def test_a_file_in_the_folder_is_captured_as_a_file_url(
    ws: Workspace, tmp_path: Path
) -> None:
    renderer = _FakeRenderer(_capture())
    shots = tmp_path / "shots"
    tool = bind(Screenshot(ANYWHERE, renderer, shots))

    result = await tool.call(
        {"url": "index.html", "width": 390, "height": 800}, ToolContext(ws)
    )

    assert result.ok, result.content
    (url, options) = renderer.captured[0]
    assert url == (tmp_path / "index.html").as_uri()
    assert options["files_under"] == tmp_path and options["width"] == 390
    written = [p for p in shots.iterdir() if p.suffix == ".png"]
    assert len(written) == 1 and written[0].read_bytes() == PNG
    assert "index-html-390" in written[0].name
    assert str(written[0]) in result.content


async def test_the_reading_says_what_a_text_only_model_can_act_on(ws: Workspace) -> None:
    text = reading_of(_capture(document=(640, 2200)), Path("/shots/x.png"))

    assert "title: Ada Byron" in text
    assert "WIDER THAN THE VIEWPORT by 250px" in text
    assert "h1: Ada Byron | h2: Work" in text
    assert "images: 2 (1 without alt)" in text
    assert "font Inter, sans-serif 16px" in text
    assert "console errors: none" in text and "failed requests: none" in text

    fits = reading_of(_capture(console_errors=("boom",)), Path("/shots/x.png"))
    assert "WIDER" not in fits and "console errors: boom" in fits


async def test_a_url_is_checked_against_the_address_rules(
    ws: Workspace, tmp_path: Path
) -> None:
    renderer = _FakeRenderer(_capture())
    tool = bind(Screenshot(WebSettings(), renderer, tmp_path / "shots"))

    result = await tool.call({"url": "http://127.0.0.1:8000/"}, ToolContext(ws))

    assert result.refused and "private" in result.content and renderer.captured == []


async def test_a_path_outside_the_folder_is_refused(ws: Workspace, tmp_path: Path) -> None:
    renderer = _FakeRenderer(_capture())
    tool = bind(Screenshot(ANYWHERE, renderer, tmp_path / "shots"))

    result = await tool.call({"url": "../../etc/passwd"}, ToolContext(ws))
    assert result.refused and renderer.captured == []

    other = await tool.call({"url": "ftp://example.com/x"}, ToolContext(ws))
    assert other.refused and "ftp" in other.content

    missing = await tool.call({"url": "nope.html"}, ToolContext(ws))
    assert not missing.ok and not missing.refused


async def test_without_a_browser_the_tool_says_how_to_get_one(
    ws: Workspace, tmp_path: Path
) -> None:
    from harness.tools.browser import INSTALL

    renderer = _FakeRenderer(
        error=RenderUnavailable(f"no browser. Install one with: {INSTALL}")
    )
    tool = bind(Screenshot(ANYWHERE, renderer, tmp_path / "shots"))

    result = await tool.call({"url": "index.html"}, ToolContext(ws))
    assert not result.ok and "install-browser" in result.content

    broken = _FakeRenderer(error=RenderFailed("the browser could not load it"))
    result = await bind(Screenshot(ANYWHERE, broken, tmp_path / "shots")).call(
        {"url": "index.html"}, ToolContext(ws)
    )
    assert not result.ok and "could not load" in result.content

    none = bind(Screenshot(ANYWHERE, None, tmp_path / "shots"))
    result = await none.call({"url": "index.html"}, ToolContext(ws))
    assert not result.ok and "install-browser" in result.content


async def test_the_viewport_has_a_ceiling_and_a_floor(ws: Workspace, tmp_path: Path) -> None:
    from harness.tools.screenshot import Shot

    tool = bind(Screenshot(ANYWHERE, _FakeRenderer(_capture()), tmp_path / "shots"))

    huge = await tool.call({"url": "index.html", "width": 10_000}, ToolContext(ws))
    assert huge.refused and "at most" in huge.content

    # The floor is the schema's, which the registry enforces before `run` is called.
    properties = Shot.schema()["properties"]
    assert isinstance(properties, dict)
    assert properties["width"]["minimum"] == 200 and properties["height"]["minimum"] == 200


def test_a_file_page_may_load_from_its_folder_and_nowhere_else(tmp_path: Path) -> None:
    beside = (tmp_path / "styles.css").resolve()
    assert file_error(beside.as_uri(), tmp_path.resolve()) == ""
    assert "outside" in file_error("file:///etc/passwd", tmp_path.resolve())
    assert "not in the working folder" in file_error(beside.as_uri(), None)
    assert "names a host" in file_error("file://server/share/x.css", tmp_path.resolve())


def test_the_kit_offers_it_beside_the_web_tools(tmp_path: Path) -> None:
    names = [t.spec.name for t in Toolkit().tools()]
    assert "screenshot" in names
    assert names.index("screenshot") == names.index("open_url") + 1
    assert screenshot_tools()[0].spec.mutates is False


async def test_the_real_browser_captures_a_local_page(tmp_path: Path) -> None:
    """The one test that starts Chromium: a page with a stylesheet beside it, a missing
    image, and a console error, so every field of the reading is exercised for real."""
    _ = pytest.importorskip("playwright")
    from harness.tools.browser import new_renderer

    _ = (tmp_path / "styles.css").write_text(
        "body { background: rgb(1, 2, 3); color: rgb(250, 250, 250); font-family: serif }"
        + ".wide { width: 900px }"
    )
    _ = (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><title>Wide</title>"
        + '<link rel="stylesheet" href="styles.css"></head>'
        + "<body><header><nav><a href='#w'>w</a></nav></header><main>"
        + "<h1>Hello</h1><div class='wide'>x</div><img src='missing.png'>"
        + '<link rel="stylesheet" href="file:///etc/hosts">'
        + "<script>console.error('bad')</script>"
        + "</main></body></html>"
    )
    renderer = new_renderer(ANYWHERE)
    try:
        try:
            shot = await renderer.capture(
                (tmp_path / "index.html").as_uri(), width=390, height=600, files_under=tmp_path
            )
        except RenderUnavailable as exc:
            pytest.skip(str(exc))
    finally:
        await renderer.aclose()

    assert shot.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert shot.title == "Wide" and shot.viewport == (390, 600)
    assert shot.document[0] >= 900  # the stylesheet loaded, so the page is wider than 390
    assert shot.headings == ("h1: Hello",)
    assert "header=1 nav=1 main=1" in shot.landmarks
    assert shot.images == 1 and shot.images_without_alt == 1
    assert shot.background == "rgb(1, 2, 3)" and "serif" in shot.font
    assert any("bad" in e for e in shot.console_errors)
    assert any("missing.png" in f for f in shot.failed_requests)
    # The stylesheet from outside the folder was refused by the guard, not served.
    assert any("etc/hosts" in f for f in shot.failed_requests)
