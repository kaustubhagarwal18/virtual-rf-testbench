"""Fixtures shared by every test suite."""

from collections.abc import Callable

import pytest

from measurements.junit import record_in_junit
from measurements.record import Measurement


@pytest.fixture
def record_measurements(
    record_property: Callable[[str, object], None],
) -> Callable[..., None]:
    """Record measurements in the JUnit XML, then fail the test if any is outside its limit.

    Args:
        record_property: pytest's fixture for adding properties to the JUnit XML.

    Returns:
        A function that records one or more measurements.
    """

    def record(*measurements: Measurement) -> None:
        """Record every measurement, then fail listing each one that missed its limit.

        Args:
            *measurements: The measurements to record.
        """
        failed = record_in_junit(record_property, measurements)
        if failed:
            pytest.fail("\n".join(measurement.describe() for measurement in failed))

    return record
