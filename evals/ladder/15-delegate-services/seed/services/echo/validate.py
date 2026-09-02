"""Display names: three to twenty characters, once surrounding whitespace is removed."""


def validate(name: str) -> list[str]:
    if not isinstance(name, str):
        return ["name: must be text"]
    errors = []
    if len(name) < 3:
        errors.append("name: at least three characters")
    cleaned = name.strip()
    if len(cleaned) > 20:
        errors.append("name: at most twenty characters")
    return errors
