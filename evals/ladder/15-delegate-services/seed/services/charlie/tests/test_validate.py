from services.charlie.validate import validate


def test_a_complete_config_has_no_errors():
    assert validate({"host": "h", "port": 80, "name": "n"}) == []


def test_each_missing_key_is_named():
    assert validate({"host": "h"}) == ["port: required", "name: required"]


def test_a_port_out_of_range_is_an_error():
    assert validate({"host": "h", "port": 70000, "name": "n"}) == ["port: must be 1-65535"]
