"""Simulated firmware releases that change the generator's behaviour.

Each simulator version plays a firmware release under test. A release listed here changes
what the generator does; every other version behaves as the baseline. Releases are looked up
by exact name, so a real version bump never picks up a simulated regression by accident.
"""

from typing import NoReturn, Protocol

from simulators.common.errors import ScpiCommandError, ScpiError, ScpiErrorCode
from simulators.common.instrument import ScpiInstrument

CHANGED_OUTPUT_RELEASE = "0.2.0-rc1"
"""A release candidate whose RF output command needs its :STATe keyword."""


class ReleaseChange(Protocol):
    """One change a release makes to the generator."""

    def apply(self, instrument: ScpiInstrument) -> None:
        """Make the change on a freshly built generator.

        Args:
            instrument: The generator, with its baseline commands registered.
        """


def _undefined_header(parameters: tuple[str, ...]) -> NoReturn:
    """Refuse a command this release no longer has, as an unknown header.

    Args:
        parameters: The command's parameters, ignored.

    Raises:
        ScpiCommandError: Always, with -113.
    """
    del parameters
    raise ScpiCommandError(ScpiError(ScpiErrorCode.UNDEFINED_HEADER))


class OutputNeedsStateKeyword:
    """OUTPut without :STATe becomes an undefined header; OUTPut:STATe keeps working.

    Drivers that send the short form 'OUTP ON', as most do, get -113: the firmware
    regression the suite has to catch.
    """

    def apply(self, instrument: ScpiInstrument) -> None:
        """Register the bare OUTPut form after the baseline one, so it takes precedence.

        Args:
            instrument: The generator.
        """
        instrument.register("OUTPut", write=_undefined_header, query=_undefined_header)


RELEASE_CHANGES: dict[str, tuple[ReleaseChange, ...]] = {
    CHANGED_OUTPUT_RELEASE: (OutputNeedsStateKeyword(),),
}
"""The changes each simulated release makes, by exact firmware version."""


def apply_release_changes(instrument: ScpiInstrument, firmware_version: str) -> None:
    """Apply the changes of the release a generator runs, if it is a listed one.

    Args:
        instrument: The generator, with its baseline commands registered.
        firmware_version: The release it reports.
    """
    for change in RELEASE_CHANGES.get(firmware_version, ()):
        change.apply(instrument)
