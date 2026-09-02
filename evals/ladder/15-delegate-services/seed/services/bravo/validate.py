"""Order lines: a sku and a quantity."""


def validate(line: dict) -> list[str]:
    errors = []
    sku = line.get("sku", "")
    if not isinstance(sku, str) or len(sku) != 8:
        errors.append("sku: must be eight characters")
    quantity = line.get("quantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
        errors.append("quantity: must be a positive whole number")
    return errors
