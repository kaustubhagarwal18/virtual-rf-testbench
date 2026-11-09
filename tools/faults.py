"""The fault catalogue: each fault the bench can be put into, how, and its known cause.

The label (category, cause and fix) is what a person would record when closing the ticket.
It is written next to a saved run, never into the run's results, so triage can't see it.
"""

from dataclasses import dataclass
from typing import Protocol

from simulators.common.faults import (
    EXTRA_CABLE_LOSS,
    LINK_DOWN,
    LINK_FAILS_EVERY,
    SLOW_RESPONSES,
    FaultDefinition,
)

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
        definition: The switch the simulator offers, which carries its name and unit, so the
            two never drift apart.
        label: The known cause.
        history_sizes: The sizes of the history runs in the data set.
        question_sizes: The sizes of the question runs, none of them a history size; the
            first is what `make fault` uses when no size is given.
        selection: Extra pytest arguments, if the run needs a subset.
    """

    name: str
    instrument: str
    definition: FaultDefinition
    label: Label
    history_sizes: tuple[float, ...]
    question_sizes: tuple[float, ...]
    selection: tuple[str, ...] = ()

    @property
    def unit(self) -> str:
        """The unit the fault's size is in.

        Returns:
            The switch's unit, for example 'dB'.
        """
        return self.definition.unit

    def suite_environment(self, size: float | None) -> dict[str, str]:
        """Return the environment the suite runs with: nothing, for an instrument fault.

        Args:
            size: The fault's size, unused.

        Returns:
            An empty environment.
        """
        del size
        return {}

    def apply(self, bench: Bench, size: float | None, default_version: str) -> str:
        """Switch the fault on, after clearing every fault.

        Args:
            bench: The running bench.
            size: The fault's size; None uses the question size.
            default_version: The bench's normal release, which the bench is put back on
                first: a run left on another release would be saved under this fault's label.

        Returns:
            The release the bench runs during the fault: its normal one.
        """
        bench.start(default_version)
        clear_all(bench)
        value = self.question_sizes[0] if size is None else size
        bench.send_admin(self.instrument, f"FAULT {self.definition.name} {value:g}")
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
        question_sizes: One None per question run.
    """

    name: str
    firmware_version: str
    label: Label
    unit: str = ""
    history_sizes: tuple[None, ...] = (None, None, None)
    question_sizes: tuple[None, ...] = (None, None, None)
    selection: tuple[str, ...] = ()

    def suite_environment(self, size: float | None) -> dict[str, str]:
        """Return the environment the suite runs with: nothing, for a firmware fault.

        Args:
            size: Unused: a release has no size.

        Returns:
            An empty environment.
        """
        del size
        return {}

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


@dataclass(frozen=True)
class ConfigFault:
    """A bench configuration that is wrong in a way someone really does get wrong.

    The instruments are healthy; the station's settings are not. The suite's setup sequence
    fails on a step, and only the sequence log says which parameter and why.

    Attributes:
        name: The fault's name in the catalogue.
        variables: The configuration the suite runs with.
        label: The known cause.
        unit: Empty: a configuration has no size.
        history_sizes: One None per history run.
        question_sizes: One None per question run.
        selection: Extra pytest arguments, if the run needs a subset.
    """

    name: str
    variables: dict[str, str]
    label: Label
    unit: str = ""
    history_sizes: tuple[None, ...] = (None, None, None)
    question_sizes: tuple[None, ...] = (None, None, None)
    selection: tuple[str, ...] = ()

    def suite_environment(self, size: float | None) -> dict[str, str]:
        """Return the broken configuration the suite runs with.

        Args:
            size: Unused: a configuration has no size.

        Returns:
            The environment variables that carry it.
        """
        del size
        return dict(self.variables)

    def apply(self, bench: Bench, size: float | None, default_version: str) -> str:
        """Put the bench on its normal release with no fault: only the settings are wrong.

        Args:
            bench: The bench.
            size: Unused.
            default_version: The bench's normal release.

        Returns:
            That release.
        """
        del size
        bench.start(default_version)
        clear_all(bench)
        return default_version

    def restore(self, bench: Bench, default_version: str) -> None:
        """Switch every instrument fault off; the configuration goes with the run's environment.

        Args:
            bench: The bench.
            default_version: Unused: the release never changed.
        """
        del default_version
        clear_all(bench)


def clear_all(bench: Bench) -> None:
    """Switch every fault off on both instruments.

    Args:
        bench: The running bench.
    """
    for instrument in (GENERATOR, ANALYZER):
        bench.send_admin(instrument, "CLEAR")


Fault = AdminFault | ConfigFault | ReleaseFault

CATALOGUE: dict[str, Fault] = {
    fault.name: fault
    for fault in (
        AdminFault(
            name="cable_loss",
            instrument=ANALYZER,
            definition=EXTRA_CABLE_LOSS,
            label=Label(
                "bench",
                "Extra loss in the cable between generator and analyzer, from a damaged cable",
                "Replaced the cable and recalibrated the path loss",
            ),
            history_sizes=(0.8, 1.2, 2.0),
            question_sizes=(1.5, 1.0, 2.5),
        ),
        AdminFault(
            name="slow_responses",
            instrument=GENERATOR,
            definition=SLOW_RESPONSES,
            label=Label(
                "test",
                "The test's 2 s instrument timeout is shorter than the generator's response time",
                "Raised the session timeout to cover the instrument's specified response time",
            ),
            history_sizes=(2.5, 3.0, 4.0),
            question_sizes=(3.5, 2.2, 5.0),
            selection=("-m", "smoke"),
        ),
        AdminFault(
            name="link_down",
            instrument=ANALYZER,
            definition=LINK_DOWN,
            label=Label(
                "bench",
                "Nothing is connected between the generator and the analyzer",
                "Reconnected the cable between the instruments and recalibrated",
            ),
            history_sizes=(1.0, 1.0, 1.0),
            question_sizes=(1.0, 1.0, 1.0),
        ),
        AdminFault(
            name="link_flaky",
            instrument=ANALYZER,
            definition=LINK_FAILS_EVERY,
            label=Label(
                "bench",
                "A loose connector: the signal disappears for one sweep in a few, so a "
                "measurement fails for no reason visible in its own numbers",
                "Reseated and replaced the connector, then reran the suite",
            ),
            history_sizes=(2.0, 3.0, 5.0),
            question_sizes=(4.0, 6.0, 7.0),
        ),
        ConfigFault(
            name="config_parameter_missing",
            variables={"BENCH_LEVEL_OFFSET_DB": ""},
            label=Label(
                "test",
                "The bench configuration has no level_offset_db, so the setup sequence "
                "can't check what offset the generator should carry",
                "Set level_offset_db in the bench configuration",
            ),
        ),
        ConfigFault(
            name="config_parameter_out_of_range",
            variables={"BENCH_LEVEL_OFFSET_DB": "80"},
            label=Label(
                "test",
                "The bench configuration asks for an 80 dB level offset, beyond the "
                "generator's +-50 dB range",
                "Corrected level_offset_db to a value the generator accepts",
            ),
        ),
        ConfigFault(
            name="config_decimal_error",
            variables={"BENCH_LEVEL_OFFSET_DB": "15"},
            label=Label(
                "test",
                "The bench configuration asks for a 15 dB level offset where 1.5 dB was "
                "meant: a misplaced decimal point, and far more than the cable's loss",
                "Corrected level_offset_db to 1.5",
            ),
        ),
        ReleaseFault(
            name="reset_leaks_offset",
            firmware_version="0.2.0-rc2",
            label=Label(
                "firmware",
                "Release 0.2.0-rc2 no longer clears the level offset on *RST, so an offset "
                "set by one test is still there for the tests that follow",
                "Reported the regression; the release restored *RST clearing the offset",
            ),
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
