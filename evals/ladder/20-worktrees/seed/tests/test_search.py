from notes.cli import COMMANDS, main


def test_search_is_registered():
    assert "search" in COMMANDS


def test_search_matches_case_insensitively(store_path, capsys):
    assert main(["--store", store_path, "search", "milk"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert "Buy milk" in lines[0] and "Milk the deadline" in lines[1]


def test_search_with_no_match_prints_nothing_and_succeeds(store_path, capsys):
    assert main(["--store", store_path, "search", "zebra"]) == 0
    assert capsys.readouterr().out == ""
