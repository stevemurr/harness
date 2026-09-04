import pytest

from notes import store


@pytest.fixture
def store_path(tmp_path):
    path = tmp_path / "notes.json"
    store.add(path, "Buy milk")
    store.add(path, "Call the plumber about the kitchen tap")
    store.add(path, "Milk the deadline for the report")
    return str(path)
