"""Tests for writing measurements into pytest's JUnit XML."""

import json

from measurements.junit import record_in_junit
from measurements.record import Limit, Measurement

VERSIONS = {"generator": "1.0.0", "analyzer": "1.0.0"}


def test_every_measurement_is_recorded_and_failures_are_returned() -> None:
    """All measurements are recorded, even after one fails; the failing ones come back."""
    recorded: list[tuple[str, object]] = []
    level_miss = Measurement("level", -13.0, "dBm", Limit.window(-11.0, 0.5), VERSIONS)
    frequency_ok = Measurement("frequency", 1e9, "Hz", Limit.window(1e9, 1e3), VERSIONS)

    def record_property(name: str, value: object) -> None:
        """Keep what would go into the JUnit XML."""
        recorded.append((name, value))

    failed = record_in_junit(record_property, [level_miss, frequency_ok])

    assert [name for name, _ in recorded] == ["measurement:level", "measurement:frequency"]
    distance_below_window_db = 1.5
    assert json.loads(str(recorded[0][1]))["margin"] == -distance_below_window_db
    assert failed == [level_miss]
