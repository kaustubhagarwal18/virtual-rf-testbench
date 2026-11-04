"""Standard JSON text: JSON has no NaN or infinity, so those become strings.

Python's json module writes NaN and Infinity as bare words by default, which standard JSON
readers reject. Everything the simulators send as JSON goes through to_json instead.
"""

import json
import math


def json_safe(value: object) -> object:
    """Replace NaN and infinities, at any depth, with the strings 'NaN', 'Infinity' and '-Infinity'.

    Args:
        value: Plain data: dicts, lists, tuples, numbers, strings, booleans or None.

    Returns:
        The same data with every non-finite float replaced; tuples become lists, as JSON has
        no tuples.
    """
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def to_json(value: object, *, stringify_unknown: bool = False) -> str:
    """Write plain data as one line of standard JSON.

    Args:
        value: The data to write.
        stringify_unknown: Write values JSON has no type for with str() instead of failing;
            logging uses this, so a log call never fails over an unusual extra field.

    Returns:
        The JSON text, with non-finite numbers written as strings.
    """
    return json.dumps(json_safe(value), allow_nan=False, default=str if stringify_unknown else None)
