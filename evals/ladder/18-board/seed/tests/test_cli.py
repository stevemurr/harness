from ledger.cli import main


def test_list_prints_one_line_per_record(capsys):
    assert main(["--file", "ledger.txt", "list"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 5
    assert lines[0].startswith("2026-01-03")
    assert "3200.00" in lines[1]
