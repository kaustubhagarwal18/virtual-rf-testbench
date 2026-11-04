"""The generator and the analyzer sharing one event loop and a small thread pool."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from measurements.record import Limit, Measurement
from simulators.common.physics import BenchParameters, cable_loss_db
from simulators.common.server import bound_port
from simulators.signal_generator import app as generator_app
from simulators.spectrum_analyzer import app as analyzer_app
from tests.utilities.tcp import ask_line

LEVEL_TOLERANCE_DB = 0.5
CONCURRENT_QUERIES = 3
SMALL_POOL_THREADS = 2
VERSIONS = {"generator": "1.0.0", "analyzer": "1.0.0"}


async def measure_concurrently() -> list[str]:
    """Start both instruments on this loop and ask the analyzer for several peaks at once.

    Returns:
        One peak level reply per concurrent query.
    """
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(SMALL_POOL_THREADS))
    generator_scpi, generator_admin = await generator_app.start(
        generator_app.Settings(
            host="127.0.0.1",
            scpi_port=0,
            firmware_version="1.0.0",
            serial_number="100001",
            admin_port=0,
        )
    )
    analyzer, analyzer_admin = await analyzer_app.start(
        analyzer_app.Settings(
            host="127.0.0.1",
            scpi_port=0,
            firmware_version="1.0.0",
            serial_number="200001",
            generator_admin_host="127.0.0.1",
            generator_admin_port=bound_port(generator_admin),
            admin_port=0,
        )
    )
    async with generator_scpi, generator_admin, analyzer, analyzer_admin:
        await ask_line(bound_port(generator_scpi), "POW -10;OUTP ON;*OPC?")
        return list(
            await asyncio.gather(
                *(
                    ask_line(bound_port(analyzer), "CALC:MARK:MAX;CALC:MARK:Y?")
                    for _ in range(CONCURRENT_QUERIES)
                )
            )
        )


def test_concurrent_measurements_on_one_loop_and_a_small_pool(
    record_measurements: Callable[..., None],
) -> None:
    """Commands queued for one instrument don't hold threads the generator's admin port needs."""
    expected_dbm = -10.0 - cable_loss_db(1e9, BenchParameters().cable_loss_at_1ghz_db)
    record_measurements(
        *(
            Measurement(
                f"level_1ghz_concurrent_{index}",
                float(reply),
                "dBm",
                Limit.window(expected_dbm, LEVEL_TOLERANCE_DB),
                VERSIONS,
            )
            for index, reply in enumerate(asyncio.run(measure_concurrently()))
        )
    )
