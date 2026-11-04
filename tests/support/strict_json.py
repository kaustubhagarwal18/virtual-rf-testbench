"""Reading JSON the way a strict, standards-following reader does: NaN and Infinity are errors."""

import json
from typing import Any


def _reject_constant(name: str) -> object:
    """Refuse a non-standard constant.

    Args:
        name: The constant json.loads found: NaN, Infinity or -Infinity.

    Raises:
        ValueError: Always.
    """
    raise ValueError(f"not standard JSON: {name}")


def strict_loads(text: str) -> Any:
    """Parse JSON text, failing on NaN, Infinity or -Infinity as standard JSON readers do.

    Args:
        text: The JSON text.

    Returns:
        The parsed data.
    """
    return json.loads(text, parse_constant=_reject_constant)
