"""The steps themselves. Each takes text and returns text."""


class Step:
    """One transformation."""

    def apply(self, text: str) -> str:
        raise NotImplementedError


class Upper(Step):
    def apply(self, text: str) -> str:
        return text.upper()


class Strip(Step):
    def apply(self, text: str) -> str:
        return text.strip()


class Squeeze(Step):
    """Collapse runs of whitespace into single spaces."""

    def apply(self, text: str) -> str:
        return " ".join(text.split())
