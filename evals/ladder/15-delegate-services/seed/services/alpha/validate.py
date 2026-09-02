"""User records: a name and an email."""


def validate(record: dict) -> list[str]:
    errors = []
    name = record.get("name", "")
    if not isinstance(name, str) or not name.strip():
        errors.append("name: required")
    email = record.get("email", "")
    if not isinstance(email, str) or "@" not in email:
        errors.append("email: must be an address")
    return errors
