"""Test-code defects the fault tooling can switch on, to build labelled triage data.

A real suite has no such switch: the defect would be in the code, put there by a change
nobody meant to break anything. Here it is one environment variable, so the data set can
hold a failure whose cause is the test code and whose fix is in it, which no instrument
or configuration fault can produce.
"""

import os
from collections.abc import Mapping

SKIP_RESET_VARIABLE = "BENCH_DEFECT_SKIP_RESET"
"""Set to 1 to open each test's sessions without *RST, as if a fixture change dropped it."""


def reset_on_open(environ: Mapping[str, str] | None = None) -> bool:
    """Say whether the bench fixture resets both instruments when it opens them.

    Args:
        environ: Where the switch is read from; None reads the process environment.

    Returns:
        False only when the skip-reset defect is switched on with the value 1.
    """
    source = os.environ if environ is None else environ
    return source.get(SKIP_RESET_VARIABLE, "") != "1"
