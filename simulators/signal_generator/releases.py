"""Simulated firmware releases that change the generator's behaviour.

Each simulator version plays a firmware release under test. A release listed here changes
what the generator does; every other version behaves as the baseline. Releases are looked up
by exact name, so a real version bump never picks up a simulated regression by accident.
"""

from typing import NoReturn, Protocol, runtime_checkable

from simulators.common.errors import ScpiCommandError, ScpiError, ScpiErrorCode
from simulators.common.instrument import ScpiInstrument

CHANGED_OUTPUT_RELEASE = "0.2.0-rc1"
"""A release candidate whose RF output command needs its :STATe keyword."""

LEAKY_RESET_RELEASE = "0.2.0-rc2"
"""A release candidate whose *RST no longer clears the level offset."""


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


@runtime_checkable
class LevelOffsetInstrument(Protocol):
    """An instrument with a level offset that *RST normally clears."""

    def keep_offset_across_reset(self) -> None:
        """Stop *RST from clearing the offset."""


class ResetKeepsTheLevelOffset:
    """*RST no longer clears the level offset, so one test's offset reaches the next.

    A regression of this shape is nasty on a bench: the test that sets an offset passes, and
    tests that run later read low for no reason of their own.
    """

    def apply(self, instrument: ScpiInstrument) -> None:
        """Stop the instrument clearing its offset on reset.

        Args:
            instrument: The generator.

        Raises:
            TypeError: If the instrument has no level offset to keep.
        """
        if not isinstance(instrument, LevelOffsetInstrument):
            raise TypeError(f"{type(instrument).__name__} has no level offset to keep")
        instrument.keep_offset_across_reset()


RELEASE_CHANGES: dict[str, tuple[ReleaseChange, ...]] = {
    CHANGED_OUTPUT_RELEASE: (OutputNeedsStateKeyword(),),
    LEAKY_RESET_RELEASE: (ResetKeepsTheLevelOffset(),),
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
