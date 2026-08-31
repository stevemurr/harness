from pipeline.pricing import Discount, total
from pipeline.runner import describe, run_pipeline
from pipeline.steps import Upper


def test_upper():
    assert run_pipeline("abc", ["upper"]) == "ABC"


def test_direct():
    assert Upper().apply("x") == "X"


def test_describe():
    assert describe(["upper"]) == "Upper.apply"


def test_discount_is_unrelated():
    assert Discount(50).apply(10) == 5
    assert total([10, 10], Discount(50)) == 10
