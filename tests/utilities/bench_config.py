"""The bench's own configuration: the settings a test sequence applies before it measures.

A real bench keeps this outside the test code, in a file or the station's environment, so the
same suite can run on benches with different cables and fixtures. Here it comes from the
environment, so the fault tooling can run the suite with a configuration that is wrong in a
realistic way: a parameter nobody set, a value outside the instrument's range, or a value in
the wrong unit.

A wrong configuration is a defect in the test setup, not in the firmware or the bench, which
is one of the three causes triage has to tell apart.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

LEVEL_OFFSET_VARIABLE = "BENCH_LEVEL_OFFSET_DB"
"""The level offset the generator is expected to carry, in dB."""

CHANNEL_BANDWIDTH_VARIABLE = "BENCH_CHANNEL_BANDWIDTH_HZ"
"""The channel-power bandwidth the analyzer is set to, in Hz."""

DEFAULT_LEVEL_OFFSET_DB = 0.0
"""A healthy bench compensates nothing in the generator: the calibration does that."""

DEFAULT_CHANNEL_BANDWIDTH_HZ = 1e5
"""The channel-power bandwidth a healthy bench measures over."""

GENERATOR_OFFSET_RANGE_DB = (-50.0, 50.0)
"""What the generator accepts as a level offset (OFFSET_RANGE_DB in its instrument)."""

ANALYZER_CHANNEL_BANDWIDTH_RANGE_HZ = (100.0, 100e6)
"""What the analyzer accepts as a channel bandwidth (CHANNEL_BANDWIDTH_RANGE_HZ)."""


class ConfigurationError(Exception):
    """The bench configuration is unusable; the message names the parameter and why."""


@dataclass(frozen=True)
class BenchConfig:
    """What the sequence applies to the instruments before measuring.

    Attributes:
        level_offset_db: The level offset the generator must carry.
        channel_bandwidth_hz: The bandwidth channel power is measured over.
    """

    level_offset_db: float
    channel_bandwidth_hz: float


def _number(
    environ: Mapping[str, str], name: str, default: float, valid_range: tuple[float, float]
) -> float:
    """Read one numeric parameter and check it against the instrument's range.

    Args:
        environ: Where the configuration comes from.
        name: The parameter's variable name.
        default: What a bench that says nothing means.
        valid_range: The lowest and highest value the instrument accepts.

    Returns:
        The value.

    Raises:
        ConfigurationError: If the parameter is present but empty, isn't a number, or lies
            outside what the instrument can be set to.
    """
    text = environ.get(name)
    if text is None:
        return default
    if not text.strip():
        raise ConfigurationError(f"{name} is not set in the bench configuration")
    try:
        value = float(text)
    except ValueError as error:
        raise ConfigurationError(f"{name} is not a number: {text!r}") from error
    low, high = valid_range
    if not low <= value <= high:
        raise ConfigurationError(
            f"{name}={value:g} is outside the instrument's range of {low:g} to {high:g}"
        )
    return value


def load_bench_config(environ: Mapping[str, str] | None = None) -> BenchConfig:
    """Read the bench configuration.

    Args:
        environ: Where to read it from; None reads the process environment.

    Returns:
        The configuration, with a healthy bench's values where nothing is set.

    Raises:
        ConfigurationError: If a parameter that is present can't be used.
    """
    source = os.environ if environ is None else environ
    return BenchConfig(
        level_offset_db=_number(
            source, LEVEL_OFFSET_VARIABLE, DEFAULT_LEVEL_OFFSET_DB, GENERATOR_OFFSET_RANGE_DB
        ),
        channel_bandwidth_hz=_number(
            source,
            CHANNEL_BANDWIDTH_VARIABLE,
            DEFAULT_CHANNEL_BANDWIDTH_HZ,
            ANALYZER_CHANNEL_BANDWIDTH_RANGE_HZ,
        ),
    )
