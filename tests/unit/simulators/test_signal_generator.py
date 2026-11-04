"""Tests for the simulated signal generator's SCPI commands."""

import pytest

from simulators.common.physics import SourceState
from simulators.signal_generator.instrument import SignalGeneratorScpi
from simulators.signal_generator.releases import CHANGED_OUTPUT_RELEASE, RELEASE_CHANGES


def make_generator() -> SignalGeneratorScpi:
    """Return a generator with a known firmware version and serial number."""
    return SignalGeneratorScpi(firmware_version="1.2.3", serial_number="100001")


def errors_of(generator: SignalGeneratorScpi) -> list[str]:
    """Read the error queue until it reports no error, and return what was in it."""
    errors: list[str] = []
    while (entry := generator.process("SYST:ERR?")[0]) != '0,"No error"':
        errors.append(entry)
    return errors


def settings_of(generator: SignalGeneratorScpi) -> list[str]:
    """Return frequency, level and output state as the generator reports them."""
    return generator.process("FREQ?;POW?;OUTP?")


def test_identity_reports_model_and_firmware_version() -> None:
    """*IDN? names VirtualRF, the SG-1 model, the serial number and the firmware version."""
    assert make_generator().process("*IDN?") == ["VirtualRF,SG-1,100001,1.2.3"]


def test_starts_at_safe_defaults() -> None:
    """A new generator sits at 1 GHz and -30 dBm with RF off."""
    assert settings_of(make_generator()) == ["1000000000", "-30", "0"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("SOUR:FREQ 2.5 GHz", "2500000000"),
        ("SOURce:FREQuency:CW 100 MHz", "100000000"),
        ("FREQ 9 kHz", "9000"),
        ("freq 6e9", "6000000000"),
    ],
)
def test_frequency_can_be_set(command: str, expected: str) -> None:
    """Frequency accepts units, short and long forms, and the full 9 kHz to 6 GHz range."""
    generator = make_generator()
    generator.process(command)
    assert generator.process("SOUR:FREQ?") == [expected]
    assert not errors_of(generator)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("SOUR:POW -10.5 dBm", "-10.5"),
        ("SOURce:POWer:LEVel:IMMediate:AMPLitude -20", "-20"),
        ("POW 20", "20"),
        ("POW -120", "-120"),
    ],
)
def test_level_can_be_set(command: str, expected: str) -> None:
    """Level accepts dBm, every optional node, and the full -120 to +20 dBm range."""
    generator = make_generator()
    generator.process(command)
    assert generator.process("POW?") == [expected]
    assert not errors_of(generator)


@pytest.mark.parametrize(
    ("command", "expected"), [("OUTP ON", "1"), ("OUTPut:STATe 1", "1"), ("OUTP OFF", "0")]
)
def test_rf_output_switches(command: str, expected: str) -> None:
    """The RF output switches with ON, OFF, 1 and 0."""
    generator = make_generator()
    generator.process(command)
    assert generator.process("OUTP?") == [expected]


@pytest.mark.parametrize(
    ("command", "error"),
    [
        ("FREQ 8 kHz", '-222,"Data out of range;8000 Hz"'),
        ("FREQ 6.1 GHz", '-222,"Data out of range;6100000000 Hz"'),
        ("POW 20.1", '-222,"Data out of range;20.1 dBm"'),
        ("POW -121", '-222,"Data out of range;-121 dBm"'),
        ("FREQ 10 dBm", '-131,"Invalid suffix;dBm"'),
        ("OUTP MAYBE", '-104,"Data type error;MAYBE"'),
        ("FREQ", '-109,"Missing parameter"'),
    ],
)
def test_bad_values_are_rejected_and_change_nothing(command: str, error: str) -> None:
    """A bad value queues its error and leaves every setting as it was."""
    generator = make_generator()
    generator.process(command)
    assert errors_of(generator) == [error]
    assert settings_of(generator) == ["1000000000", "-30", "0"]


def test_reset_restores_the_defaults() -> None:
    """*RST puts frequency, level and output back to their defaults."""
    generator = make_generator()
    generator.process("FREQ 2 GHz;POW -5;OUTP ON")
    generator.process("*RST")
    assert settings_of(generator) == ["1000000000", "-30", "0"]


def test_source_state_matches_the_settings() -> None:
    """source_state() reports what the generator is sending, for the analyzer."""
    generator = make_generator()
    generator.process("FREQ 2 GHz;POW -10;OUTP ON")
    assert generator.source_state() == SourceState(
        frequency_hz=2e9, level_dbm=-10.0, output_on=True
    )


def test_release_candidate_drops_the_short_output_command() -> None:
    """In 0.2.0-rc1, OUTP without :STATe is an undefined header; OUTP:STAT still works."""
    generator = SignalGeneratorScpi(firmware_version=CHANGED_OUTPUT_RELEASE)
    assert not generator.process("OUTP ON")
    assert not generator.process("OUTP?")
    assert errors_of(generator) == ['-113,"Undefined header"'] * 2
    assert generator.process("OUTP:STAT ON;OUTP:STAT?") == ["1"]
    assert generator.source_state().output_on


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "0.2.0-rc2", "9.9.9"])
def test_every_other_release_keeps_the_short_output_command(version: str) -> None:
    """Only the named release changes the command; similar version strings don't."""
    generator = SignalGeneratorScpi(firmware_version=version)
    assert generator.process("OUTP ON;OUTP?") == ["1"]
    assert not errors_of(generator)


def test_the_changed_release_is_listed() -> None:
    """The release table names each simulated release and what it changes."""
    assert CHANGED_OUTPUT_RELEASE in RELEASE_CHANGES
