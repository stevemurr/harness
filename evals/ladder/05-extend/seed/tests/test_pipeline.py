import pytest

from pipeline.runner import run_pipeline


def test_upper():
    assert run_pipeline("abc", ["upper"]) == "ABC"


def test_chain():
    assert run_pipeline("  a   b  ", ["strip", "squeeze"]) == "a b"


def test_unknown_step():
    with pytest.raises(KeyError):
        run_pipeline("x", ["nope"])
