"""Faults switched on the admin ports change what the drivers see, as on a faulty bench."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from drivers.session import (
    InstrumentError,
    InstrumentTimeoutError,
    RsInstrumentSession,
    socket_resource,
)
from tests.integration.conftest import RunningBench
from tests.integration.processes import ask
from tests.utilities.bench import measure_peak_at, open_bench

EXTRA_LOSS_DB = 2.0
READING_TOLERANCE_DB = 0.1
SLOW_RESPONSE_S = 0.6
SHORT_TIMEOUT_MS = 300


def admin(port: int, line: str) -> dict[str, object]:
    """Send one admin command and return its JSON reply."""
    reply: dict[str, object] = json.loads(ask(port, line))
    return reply


def test_extra_cable_loss_lowers_the_analyzer_reading(running_bench: RunningBench) -> None:
    """With 2 dB of extra cable loss, the same carrier reads 2 dB lower."""
    with open_bench(
        "127.0.0.1", running_bench.generator.scpi_port, running_bench.analyzer.scpi_port
    ) as (generator, analyzer):
        healthy_dbm = measure_peak_at(generator, analyzer, 1e9, -10.0).level_dbm
        reply = admin(
            running_bench.analyzer.admin_port, f"FAULT EXTRA_CABLE_LOSS_DB {EXTRA_LOSS_DB}"
        )
        assert reply == {"fault": "EXTRA_CABLE_LOSS_DB", "value": EXTRA_LOSS_DB}
        faulty_dbm = measure_peak_at(generator, analyzer, 1e9, -10.0).level_dbm
    assert healthy_dbm - faulty_dbm == pytest.approx(EXTRA_LOSS_DB, abs=READING_TOLERANCE_DB)


def test_slow_responses_trip_a_short_timeout_until_cleared(running_bench: RunningBench) -> None:
    """With slow responses on, a query times out; after CLEAR, the same query answers."""
    resource = socket_resource("127.0.0.1", running_bench.generator.scpi_port)
    with RsInstrumentSession(resource, timeout_ms=SHORT_TIMEOUT_MS) as session:
        admin(running_bench.generator.admin_port, f"FAULT SLOW_RESPONSE_S {SLOW_RESPONSE_S}")
        with pytest.raises(InstrumentTimeoutError):
            session.query("SOUR:FREQ?")
        with pytest.raises(InstrumentError, match="out of step after a timeout"):
            session.query("SOUR:POW?")
    assert admin(running_bench.generator.admin_port, "CLEAR") == {"cleared": ["SLOW_RESPONSE_S"]}
    with RsInstrumentSession(resource, timeout_ms=SHORT_TIMEOUT_MS) as session:
        assert session.query("SOUR:FREQ?") == "1000000000"


def test_each_simulator_offers_only_its_own_faults(running_bench: RunningBench) -> None:
    """The generator offers slow responses; the cable and link faults belong to the analyzer."""
    generator_faults = admin(running_bench.generator.admin_port, "FAULTS?")
    analyzer_faults = admin(running_bench.analyzer.admin_port, "FAULTS?")
    assert generator_faults == {"active": {}, "available": ["SLOW_RESPONSE_S"]}
    assert analyzer_faults == {
        "active": {},
        "available": ["EXTRA_CABLE_LOSS_DB", "LINK_DOWN", "LINK_FAILS_EVERY"],
    }


def test_the_admin_port_answers_while_a_slow_scpi_line_waits(running_bench: RunningBench) -> None:
    """Slow responses delay SCPI only: CLEAR answers at once, even while a query is waiting."""
    admin(running_bench.generator.admin_port, f"FAULT SLOW_RESPONSE_S {SLOW_RESPONSE_S}")
    with ThreadPoolExecutor(1) as pool:
        waiting = pool.submit(ask, running_bench.generator.scpi_port, "*IDN?")
        time.sleep(SLOW_RESPONSE_S / 4)
        started_s = time.monotonic()
        assert admin(running_bench.generator.admin_port, "CLEAR") == {
            "cleared": ["SLOW_RESPONSE_S"]
        }
        assert time.monotonic() - started_s < SLOW_RESPONSE_S / 2
        assert waiting.result().startswith("VirtualRF,SG-1")
