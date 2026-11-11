"""Tests for the test-code defects the fault tooling can switch on."""

import pytest

from tests.utilities.injected_defects import SKIP_RESET_VARIABLE, reset_on_open


@pytest.mark.parametrize(
    ("environ", "resets"),
    [
        ({}, True),
        ({SKIP_RESET_VARIABLE: ""}, True),
        ({SKIP_RESET_VARIABLE: "0"}, True),
        ({SKIP_RESET_VARIABLE: "yes"}, True),
        ({SKIP_RESET_VARIABLE: "1"}, False),
    ],
)
def test_the_fixture_resets_unless_the_defect_is_switched_on(
    environ: dict[str, str], resets: bool
) -> None:
    """Only the exact value 1 switches the defect on; anything else leaves the reset in place."""
    assert reset_on_open(environ) is resets


def test_the_process_environment_is_read_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an explicit environment, the suite's own environment decides."""
    monkeypatch.setenv(SKIP_RESET_VARIABLE, "1")
    assert reset_on_open() is False
