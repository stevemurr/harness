from notes import store
from notes.cli import COMMANDS, main


def test_tag_is_registered():
    assert "tag" in COMMANDS


def test_tagging_a_note_saves_the_tag(store_path, capsys):
    assert main(["--store", store_path, "tag", "2", "home"]) == 0
    assert store.load(store_path)[1]["tags"] == ["home"]
    assert main(["--store", store_path, "tag", "2", "home"]) == 0  # once only
    assert store.load(store_path)[1]["tags"] == ["home"]


def test_list_can_filter_by_tag(store_path, capsys):
    main(["--store", store_path, "tag", "1", "shopping"])
    main(["--store", store_path, "tag", "3", "work"])
    capsys.readouterr()
    assert main(["--store", store_path, "list", "--tag", "work"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and "deadline" in lines[0] and "#work" in lines[0]


def test_tagging_a_missing_note_fails(store_path, capsys):
    assert main(["--store", store_path, "tag", "9", "x"]) != 0
