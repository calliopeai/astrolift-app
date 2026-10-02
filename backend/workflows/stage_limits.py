"""Pure bounds shared by workflow authoring and durable execution."""

DEFAULT_STAGE_ATTEMPTS = 3
MAX_STAGE_ATTEMPTS = 20


def validate_stage_attempts(value: object) -> int:
    """Count the initial dispatch as an attempt; reject coercion/truncation."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_STAGE_ATTEMPTS:
        raise ValueError(f"max_attempts must be an integer between 1 and {MAX_STAGE_ATTEMPTS}")
    return value
