from columns.columns import align, is_number


def test_numbers_are_recognised():
    assert is_number("12.5") and is_number("-3") and not is_number("twelve")


def test_numbers_are_flush_right():
    assert align([["qty", "1"], ["x", "250"]]).splitlines()[0] == "qty   1"


def test_text_is_flush_left():
    assert align([["name", "qty"], ["hammer", "3"]]) == "name   qty\nhammer   3"


def test_no_trailing_spaces():
    for line in align([["a", "bb"], ["ccc", "d"]]).splitlines():
        assert line == line.rstrip()


def test_an_empty_table_is_empty():
    assert align([]) == ""
