"""Tests for the fault catalogue: how each fault is applied, restored and labelled."""

from collections import Counter

import pytest

from tests.unit.tools.fakes import FakeBench
from tests.utilities.bench_config import CHANNEL_BANDWIDTH_VARIABLE, LEVEL_OFFSET_VARIABLE
from tools.faults import (
    ANALYZER,
    CATALOGUE,
    GENERATOR,
    AdminFault,
    ConfigFault,
    ReleaseFault,
)

FAULTS_PER_CATEGORY = 2
"""How many faults each cause needs, so no category can be guessed right by default."""


def test_question_sizes_differ_from_every_history_size() -> None:
    """A fault with a size uses a question size absent from history, so retrieval can't copy.

    Faults with nothing to scale, such as a disconnected cable, repeat their single value.
    """
    for fault in CATALOGUE.values():
        sizes = set(fault.history_sizes)
        if isinstance(fault, AdminFault) and len(sizes) > 1:
            assert fault.question_size not in sizes
            assert len(fault.history_sizes) == len(sizes)


def test_an_admin_fault_restores_the_baseline_release_clears_then_switches_on() -> None:
    """Applying starts the normal release, clears both instruments, then sets the switch."""
    fault = CATALOGUE["cable_loss"]
    assert isinstance(fault, AdminFault)
    bench = FakeBench()
    assert fault.apply(bench, 1.25, "0.1.0") == "0.1.0"
    fault.restore(bench, "0.1.0")
    assert bench.calls == [
        ("start", "0.1.0"),
        (GENERATOR, "CLEAR"),
        (ANALYZER, "CLEAR"),
        (ANALYZER, "FAULT EXTRA_CABLE_LOSS_DB 1.25"),
        (GENERATOR, "CLEAR"),
        (ANALYZER, "CLEAR"),
    ]


def test_an_admin_fault_without_a_size_uses_its_question_size() -> None:
    """No size means the question size."""
    fault = CATALOGUE["slow_responses"]
    bench = FakeBench()
    fault.apply(bench, None, "0.1.0")
    assert bench.calls[-1] == (GENERATOR, "FAULT SLOW_RESPONSE_S 3.5")


def test_a_release_fault_restarts_the_bench_as_the_release_and_back() -> None:
    """Applying rebuilds the bench as the faulty release; restoring rebuilds the normal one."""
    fault = CATALOGUE["output_command_change"]
    assert isinstance(fault, ReleaseFault)
    bench = FakeBench()
    assert fault.apply(bench, None, "0.1.0") == "0.2.0-rc1"
    fault.restore(bench, "0.1.0")
    assert bench.calls == [
        (GENERATOR, "CLEAR"),
        (ANALYZER, "CLEAR"),
        ("start", "0.2.0-rc1"),
        ("start", "0.1.0"),
    ]


def test_a_configuration_fault_leaves_the_instruments_alone() -> None:
    """Only the suite's environment is wrong: the bench runs its normal release, faults cleared."""
    fault = CATALOGUE["config_parameter_missing"]
    assert isinstance(fault, ConfigFault)
    bench = FakeBench()
    assert fault.apply(bench, None, "0.1.0") == "0.1.0"
    assert fault.suite_environment(None) == {"BENCH_LEVEL_OFFSET_DB": ""}
    fault.restore(bench, "0.1.0")
    assert bench.calls == [
        ("start", "0.1.0"),
        (GENERATOR, "CLEAR"),
        (ANALYZER, "CLEAR"),
        (GENERATOR, "CLEAR"),
        (ANALYZER, "CLEAR"),
    ]


@pytest.mark.parametrize("name", ["cable_loss", "output_command_change"])
def test_an_instrument_fault_needs_no_special_environment(name: str) -> None:
    """Only configuration faults change the environment the suite runs with."""
    assert CATALOGUE[name].suite_environment(None) == {}


def test_every_configured_variable_is_one_the_suite_reads() -> None:
    """A configuration fault that set an unknown variable would change nothing."""
    known = {LEVEL_OFFSET_VARIABLE, CHANNEL_BANDWIDTH_VARIABLE}
    for fault in CATALOGUE.values():
        if isinstance(fault, ConfigFault):
            assert set(fault.variables) <= known, f"{fault.name} sets something unread"


def test_the_catalogue_covers_every_category_more_than_once() -> None:
    """Each cause has at least two faults, so no answer is right by always guessing one."""
    counts = Counter(fault.label.category for fault in CATALOGUE.values())
    assert set(counts) == {"bench", "test", "firmware"}
    assert min(counts.values()) >= FAULTS_PER_CATEGORY
