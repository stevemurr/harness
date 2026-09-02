"""Service configuration: three keys are required."""

REQUIRED = ("host", "port", "name")


def validate(config: dict) -> list[str]:
    missing = [key for key in REQUIRED if key in config]
    errors = [f"{key}: required" for key in missing]
    port = config.get("port")
    if port is not None and (not isinstance(port, int) or not 1 <= port <= 65535):
        errors.append("port: must be 1-65535")
    return errors
