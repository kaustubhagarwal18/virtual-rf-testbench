"""Tests for the fault catalogue: how each fault is applied, restored and labelled."""

from tests.unit.tools.fakes import FakeBench
from tools.faults import ANALYZER, CATALOGUE, GENERATOR, AdminFault, ReleaseFault


def test_the_catalogue_covers_firmware_bench_and_test() -> None:
    """There is a fault for each of the three causes triage tells apart."""
    assert sorted(fault.label.category for fault in CATALOGUE.values()) == [
        "bench",
        "firmware",
        "test",
    ]


def test_question_sizes_differ_from_every_history_size() -> None:
    """A question run never repeats a history run's size, so retrieval can't just find a copy."""
    for fault in CATALOGUE.values():
        if isinstance(fault, AdminFault):
            assert fault.question_size not in fault.history_sizes
            assert len(fault.history_sizes) == len(set(fault.history_sizes))


def test_an_admin_fault_clears_first_then_switches_on_and_clears_after() -> None:
    """Applying clears both instruments, then sets the switch; restoring clears again."""
    fault = CATALOGUE["cable_loss"]
    assert isinstance(fault, AdminFault)
    bench = FakeBench()
    assert fault.apply(bench, 1.25, "0.1.0") == "0.1.0"
    fault.restore(bench, "0.1.0")
    assert bench.calls == [
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
