from services.bravo.validate import validate


def test_a_good_line_has_no_errors():
    assert validate({"sku": "ABCD1234", "quantity": 3}) == []


def test_a_short_sku_is_an_error():
    assert validate({"sku": "ABC", "quantity": 1}) == ["sku: must be eight characters"]


def test_zero_is_not_a_quantity():
    assert validate({"sku": "ABCD1234", "quantity": 0}) == [
        "quantity: must be a positive whole number"
    ]


def test_a_boolean_is_not_a_quantity():
    assert validate({"sku": "ABCD1234", "quantity": True}) == [
        "quantity: must be a positive whole number"
    ]
