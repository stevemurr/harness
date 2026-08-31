"""The block that tells the model where it is.

Written because a live run showed the model did not know. Across four scenarios it twice
tried to write to `/home/user`, and a test it wrote hardcoded `python`, which on that
machine is Python 2.7.18. The system prompt said "folder" twelve times and never named it.
"""

from __future__ import annotations

from pathlib import Path

from harness.environment import describe


def test_it_names_the_actual_folder(tmp_path: Path) -> None:
    """The whole bug. A model that is not told where it is invents somewhere plausible."""
    block = describe(tmp_path)

    assert str(tmp_path) in block


def test_it_says_the_folder_is_not_a_boundary(tmp_path: Path) -> None:
    """`run` is unconfined, so the model needs to know the folder is a working directory
    rather than a wall it will bounce off."""
    assert "not a sandbox" in describe(tmp_path)


def test_it_steers_to_relative_paths(tmp_path: Path) -> None:
    """Naming the folder is not enough -- it has to say what to do with the name.

    Measured 2026-08-30: the first version stated the absolute path and said relative paths
    resolve there. The model read the path and used it, mistyped it once, then wrote one
    relative path prefixed with the folder name -- creating a nested folder and splitting
    the work between the two. It burned 30+ turns lost between them where the previous
    version had finished in 20.
    """
    block = describe(tmp_path)

    assert "Use relative paths" in block
    assert "never need to `cd`" in block


def test_it_lists_what_is_there(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("hi")

    block = describe(tmp_path)

    assert "src/" in block
    assert "README.md" in block


def test_an_empty_folder_says_so_rather_than_showing_nothing(tmp_path: Path) -> None:
    assert "the folder is empty" in describe(tmp_path)


def test_hidden_files_are_not_listed(tmp_path: Path) -> None:
    """A listing dominated by dotfiles is a listing nobody reads."""
    (tmp_path / ".venv").mkdir()
    (tmp_path / "main.py").write_text("x")

    block = describe(tmp_path)

    assert "main.py" in block
    assert ".venv" not in block.split("Contents:")[1]


def test_a_long_listing_is_truncated_and_says_how_much(tmp_path: Path) -> None:
    for i in range(50):
        (tmp_path / f"file{i:02d}.txt").write_text("x")

    block = describe(tmp_path, limit=10)

    assert "+40 more" in block


def test_project_markers_are_reported_as_facts(tmp_path: Path) -> None:
    """`uv.lock` present is a fact. 'Use uv' is a preference and belongs in AGENTS.md --
    a harness with hardcoded Python opinions is wrong for the next language."""
    (tmp_path / "pyproject.toml").write_text("[project]")
    (tmp_path / "uv.lock").write_text("")

    block = describe(tmp_path)

    assert "pyproject.toml" in block
    assert "uv.lock" in block


def test_a_conventions_file_is_included_verbatim(tmp_path: Path) -> None:
    """The user's file, unparsed: summarising it would be deciding which of their
    instructions mattered."""
    (tmp_path / "AGENTS.md").write_text("Always use uv. Never edit generated/.")

    block = describe(tmp_path)

    assert "Always use uv. Never edit generated/." in block
    assert "# AGENTS.md" in block


def test_only_the_first_conventions_file_is_read(tmp_path: Path) -> None:
    """A repository that already has CLAUDE.md should not need a third file."""
    (tmp_path / "AGENTS.md").write_text("from agents")
    (tmp_path / "CLAUDE.md").write_text("from claude")

    block = describe(tmp_path)

    assert "from agents" in block
    assert "from claude" not in block


def test_an_empty_conventions_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("   \n")

    assert "# AGENTS.md" not in describe(tmp_path)


def test_it_reports_what_bare_python_actually_is(tmp_path: Path) -> None:
    """A model writing `["python", ...]` in a test is making an assumption it cannot check,
    and on a machine where `python` is 2.7 the test fails everywhere but the venv it was
    written in."""
    block = describe(tmp_path)

    assert "`python`" in block


def test_a_folder_with_no_git_says_so(tmp_path: Path) -> None:
    assert "not a repository" in describe(tmp_path)
