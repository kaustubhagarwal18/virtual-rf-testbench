"""The fault catalogue: each fault the bench can be put into, how, and its known cause.

The label (category, cause and fix) is what a person would record when closing the ticket.
It is written next to a saved run, never into the run's results, so triage can't see it.
"""

from dataclasses import dataclass
from typing import Protocol

GENERATOR = "generator"
ANALYZER = "analyzer"


class Bench(Protocol):
    """What a fault needs from the bench."""

    def send_admin(self, instrument: str, line: str) -> dict[str, object]:
        """Send one admin command to an instrument.

        Args:
            instrument: GENERATOR or ANALYZER.
            line: The command.

        Returns:
            The JSON reply.
        """

    def start(self, firmware_version: str) -> None:
        """Build and start the bench as a firmware release, and wait until it is healthy.

        Args:
            firmware_version: The release both instruments report.
        """


@dataclass(frozen=True)
class Label:
    """A fault's known cause, as a closed ticket would record it.

    Attributes:
        category: firmware, bench or test.
        cause: What went wrong.
        fix: What fixed it.
    """

    category: str
    cause: str
    fix: str


@dataclass(frozen=True)
class AdminFault:
    """A fault switched on an instrument's admin port.

    Attributes:
        name: The fault's name in the catalogue.
        instrument: GENERATOR or ANALYZER.
        switch: The admin fault switch, for example 'EXTRA_CABLE_LOSS_DB'.
        unit: The switch's unit.
        label: The known cause.
        history_sizes: The sizes of the history runs in the data set.
        question_size: The size of the question run, different from every history size.
    """

    name: str
    instrument: str
    switch: str
    unit: str
    label: Label
    history_sizes: tuple[float, ...]
    question_size: float

    def apply(self, bench: Bench, size: float | None, default_version: str) -> str:
        """Switch the fault on, after clearing every fault.

        Args:
            bench: The running bench.
            size: The fault's size; None uses the question size.
            default_version: The bench's normal release.

        Returns:
            The release the bench runs during the fault: its normal one.
        """
        clear_all(bench)
        value = self.question_size if size is None else size
        bench.send_admin(self.instrument, f"FAULT {self.switch} {value:g}")
        return default_version

    def restore(self, bench: Bench, default_version: str) -> None:
        """Switch every fault off.

        Args:
            bench: The running bench.
            default_version: The bench's normal release, unused: it never changed.
        """
        del default_version
        clear_all(bench)


@dataclass(frozen=True)
class ReleaseFault:
    """A firmware regression: the bench rebuilt as a simulated release.

    Attributes:
        name: The fault's name in the catalogue.
        firmware_version: The release that carries the regression.
        unit: Empty: a release has no size.
        label: The known cause.
        history_sizes: One None per history run; releases have no size.
        question_size: None.
    """

    name: str
    firmware_version: str
    label: Label
    unit: str = ""
    history_sizes: tuple[None, ...] = (None, None, None)
    question_size: None = None

    def apply(self, bench: Bench, size: float | None, default_version: str) -> str:
        """Rebuild and start the bench as the faulty release.

        Args:
            bench: The bench.
            size: Ignored: a release has no size.
            default_version: The bench's normal release, unused: the fault replaces it.

        Returns:
            The faulty release, which the suite then expects in *IDN?.
        """
        del size, default_version
        clear_all(bench)
        bench.start(self.firmware_version)
        return self.firmware_version

    def restore(self, bench: Bench, default_version: str) -> None:
        """Rebuild and start the bench as its normal release.

        Args:
            bench: The bench.
            default_version: The normal release.
        """
        bench.start(default_version)


def clear_all(bench: Bench) -> None:
    """Switch every fault off on both instruments.

    Args:
        bench: The running bench.
    """
    for instrument in (GENERATOR, ANALYZER):
        bench.send_admin(instrument, "CLEAR")


Fault = AdminFault | ReleaseFault

CATALOGUE: dict[str, Fault] = {
    fault.name: fault
    for fault in (
        AdminFault(
            name="cable_loss",
            instrument=ANALYZER,
            switch="EXTRA_CABLE_LOSS_DB",
            unit="dB",
            label=Label(
                "bench",
                "Extra loss in the cable between generator and analyzer, from a damaged cable",
                "Replaced the cable and recalibrated the path loss",
            ),
            history_sizes=(0.8, 1.2, 2.0),
            question_size=1.5,
        ),
        AdminFault(
            name="slow_responses",
            instrument=GENERATOR,
            switch="SLOW_RESPONSE_S",
            unit="s",
            label=Label(
                "test",
                "The test's 2 s instrument timeout is shorter than the generator's response time",
                "Raised the session timeout to cover the instrument's specified response time",
            ),
            history_sizes=(2.5, 3.0, 4.0),
            question_size=3.5,
        ),
        ReleaseFault(
            name="output_command_change",
            firmware_version="0.2.0-rc1",
            label=Label(
                "firmware",
                "Release 0.2.0-rc1 dropped the short form of the RF output command: "
                "OUTP ON now gives -113",
                "Reported the regression; the release restored OUTPut without :STATe",
            ),
        ),
    )
}
"""Every fault, by name."""
