from notes.cli import COMMANDS, main


def test_export_is_registered():
    assert "export" in COMMANDS


def test_export_writes_markdown(store_path, tmp_path, capsys):
    main(["--store", store_path, "tag", "1", "shopping"]) if "tag" in COMMANDS else None
    out = tmp_path / "notes.md"
    assert main(["--store", store_path, "export", str(out)]) == 0
    text = out.read_text()
    lines = text.strip().splitlines()
    assert lines[0] == "# Notes"
    assert lines[1] == ""
    assert lines[2].startswith("- [1] Buy milk")
    assert lines[3] == "- [2] Call the plumber about the kitchen tap"
    assert lines[4] == "- [3] Milk the deadline for the report"
    assert text.endswith("\n")


def test_export_of_an_empty_store_has_only_the_heading(tmp_path):
    out = tmp_path / "empty.md"
    assert main(["--store", str(tmp_path / "none.json"), "export", str(out)]) == 0
    assert out.read_text() == "# Notes\n"
