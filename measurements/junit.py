"""Writing measurements into pytest's JUnit XML, where CI and the triage service read them."""

import json
from collections.abc import Callable, Iterable

from measurements.record import Measurement

RecordProperty = Callable[[str, object], None]
"""pytest's record_property fixture: adds a name and value to the test's JUnit entry."""


def record_in_junit(
    record_property: RecordProperty, measurements: Iterable[Measurement]
) -> list[Measurement]:
    """Record every measurement, failing or not, and return the ones that failed.

    Args:
        record_property: pytest's record_property fixture.
        measurements: The measurements to record.

    Returns:
        The measurements outside their limits, in the order given.
    """
    failed: list[Measurement] = []
    for measurement in measurements:
        record_property(
            f"measurement:{measurement.name}",
            json.dumps(measurement.to_dict(), allow_nan=False),
        )
        if not measurement.passed:
            failed.append(measurement)
    return failed
