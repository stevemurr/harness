from services.alpha.validate import validate


def test_a_good_record_has_no_errors():
    assert validate({"name": "Ada", "email": "ada@example.org"}) == []


def test_a_blank_name_is_an_error():
    assert validate({"name": "  ", "email": "a@b.co"}) == ["name: required"]


def test_an_address_needs_a_domain_with_a_dot():
    assert validate({"name": "Ada", "email": "ada@example"}) == ["email: must be an address"]


def test_an_address_needs_an_at():
    assert validate({"name": "Ada", "email": "ada.example.org"}) == ["email: must be an address"]
