"""Reading SCPI numeric parameters, with optional units."""

import math
import re
from collections.abc import Mapping

from simulators.common.errors import ScpiCommandError, ScpiError, ScpiErrorCode

FREQUENCY_UNITS: Mapping[str, float] = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}
"""Frequency units and their multipliers to Hz. SCPI units are case-insensitive."""

LEVEL_UNITS: Mapping[str, float] = {"DBM": 1.0}
"""Level units and their multipliers to dBm."""

_NUMBER_WITH_UNIT = re.compile(
    r"\s*(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:\s*(?P<unit>[A-Za-z]+))?\s*"
)


def parse_number(text: str, units: Mapping[str, float] | None = None) -> float:
    """Read a number such as '1 GHz', '-10 dBm' or '1e9'.

    Args:
        text: The parameter as sent by the controller.
        units: The units this setting accepts, mapped to their multipliers. None means the
            value takes no unit.

    Returns:
        The value in the setting's base unit, for example Hz or dBm.

    Raises:
        ScpiCommandError: -104 if the text isn't a number, -131 if the unit isn't accepted,
            -222 if the value is too large to represent.
    """
    match = _NUMBER_WITH_UNIT.fullmatch(text)
    if match is None:
        raise ScpiCommandError(ScpiError(ScpiErrorCode.DATA_TYPE_ERROR, text))
    value = float(match["number"])
    unit = match["unit"]
    if unit is not None:
        multiplier = (units or {}).get(unit.upper())
        if multiplier is None:
            raise ScpiCommandError(ScpiError(ScpiErrorCode.INVALID_SUFFIX, unit))
        value *= multiplier
    if not math.isfinite(value):
        raise ScpiCommandError(ScpiError(ScpiErrorCode.DATA_OUT_OF_RANGE, text))
    return value
