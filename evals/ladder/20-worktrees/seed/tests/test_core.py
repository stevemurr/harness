from notes.cli import COMMANDS, main


def test_add_then_list(store_path, capsys):
    assert main(["--store", store_path, "add", "Water the plants"]) == 0
    assert main(["--store", store_path, "list"]) == 0
    out = capsys.readouterr().out
    assert "Water the plants" in out and out.count("\n") == 5


def test_commands_are_registered():
    assert {"add", "list"} <= set(COMMANDS)
