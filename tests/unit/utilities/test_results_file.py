"""Tests for the JSON results file every bench run writes."""

import json
import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.utilities.results_file import (
    ResultsCollector,
    firmware_version_from_identity,
    instrument_errors_in,
    measurements_in,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
LEVEL = {"name": "level", "value": -12.5, "unit": "dBm", "margin": -0.5, "passed": False}


@dataclass
class FakeReport:
    """A test phase's report, with only what the collector reads."""

    nodeid: str
    when: str = "call"
    outcome: str = "passed"
    duration: float = 0.1
    longrepr: object = None
    longreprtext: str = ""
    user_properties: list[tuple[str, object]] = field(default_factory=list)


def test_instrument_errors_are_found_once_each_in_order() -> None:
    """SCPI errors in a failure's text are listed in order, without repeats."""
    text = 'failed: -113,"Undefined header"; then -222,"Data out of range"; -113,"Undefined header"'
    assert instrument_errors_in(text) == ['-113,"Undefined header"', '-222,"Data out of range"']


def test_only_measurement_properties_are_read() -> None:
    """Properties named measurement:<name> become measurements; others are ignored."""
    properties = [("measurement:level", json.dumps(LEVEL)), ("owner", "bench team")]
    assert measurements_in(properties) == [LEVEL]


@pytest.mark.parametrize(
    ("identity", "version"),
    [("VirtualRF,SG-1,100001,0.2.0-rc1", "0.2.0-rc1"), ("VirtualRF,SG-1", "unknown")],
)
def test_firmware_version_is_the_fourth_identity_field(identity: str, version: str) -> None:
    """*IDN?'s fourth field is the firmware version; a short reply gives 'unknown'."""
    assert firmware_version_from_identity(identity) == version


def test_phases_combine_into_one_record_with_the_worst_outcome() -> None:
    """A test that passes its call but fails teardown is an error, with the teardown's message."""
    collector = ResultsCollector(["pytest", "tests/bench"])
    collector.add_report(FakeReport("t::a", when="setup"))
    collector.add_report(
        FakeReport("t::a", user_properties=[("measurement:level", json.dumps(LEVEL))])
    )
    collector.add_report(
        FakeReport(
            "t::a",
            when="teardown",
            outcome="failed",
            longrepr=("t.py", 3, "teardown broke"),
            longreprtext="teardown broke",
        )
    )
    (test,) = collector.to_dict()["tests"]
    assert (test["outcome"], test["message"], test["measurements"]) == (
        "error",
        "teardown broke",
        [LEVEL],
    )
    assert test["duration_s"] == pytest.approx(0.3)


def test_a_failure_message_falls_back_to_the_last_line_of_its_text() -> None:
    """Without a crash summary or a tuple, the message is the text's last line."""
    collector = ResultsCollector(["pytest"])
    collector.add_report(
        FakeReport("t::b", outcome="failed", longreprtext="traceback\nAssertionError: boom\n")
    )
    assert collector.to_dict()["tests"][0]["message"] == "AssertionError: boom"


def write_suite(folder: Path) -> None:
    """Write a tiny suite whose conftest registers the results plugin, like the bench's."""
    (folder / "conftest.py").write_text(
        textwrap.dedent(
            """
            import json, os
            from pathlib import Path
            import pytest
            from tests.utilities.results_file import ResultsCollector, ResultsFilePlugin

            def pytest_configure(config):
                collector = ResultsCollector(["pytest", *config.invocation_params.args])
                collector.firmware_versions["generator"] = "0.1.0"
                path = Path(os.environ["BENCH_RESULTS_JSON"])
                config.pluginmanager.register(ResultsFilePlugin(collector, path), "bench-results")

            @pytest.fixture
            def broken():
                raise RuntimeError("fixture broke")
            """
        )
    )
    (folder / "test_suite.py").write_text(
        textwrap.dedent(
            f"""
            import json
            import pytest

            def test_passes():
                pass

            def test_misses_its_limit(record_property):
                record_property("measurement:level", json.dumps({LEVEL!r}))
                raise AssertionError('instrument said -113,"Undefined header"')

            def test_setup_fails(broken):
                pass

            def test_skipped():
                pytest.skip("not today")

            @pytest.mark.slow
            def test_deselected():
                pass
            """
        )
    )


def test_a_real_session_writes_the_results_file(tmp_path: Path) -> None:
    """Pytest with the plugin writes every outcome, the evidence, and what was deselected."""
    write_suite(tmp_path)
    results_path = tmp_path / "results.json"
    environment = {**os.environ, "BENCH_RESULTS_JSON": str(results_path)}
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT)
    subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-m", "not slow", "-q"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
    )
    results = json.loads(results_path.read_text(encoding="utf-8"))
    outcomes = {test["id"].split("::")[-1]: test for test in results["tests"]}
    assert results["firmware_versions"] == {"generator": "0.1.0"}
    assert results["selection"]["command"] == "pytest -p no:cacheprovider -m 'not slow' -q"
    assert (results["selection"]["selected"], results["selection"]["deselected"]) == (4, 1)
    assert results["totals"] == {"run": 4, "passed": 1, "skipped": 1, "failed": 1, "error": 1}
    failed = outcomes["test_misses_its_limit"]
    assert failed["instrument_errors"] == ['-113,"Undefined header"']
    assert failed["measurements"] == [LEVEL]
    assert "instrument said" in failed["message"]
    assert outcomes["test_setup_fails"]["message"] == "RuntimeError: fixture broke"
    assert outcomes["test_skipped"]["message"] == "Skipped: not today"
