"""How the analyzer learns what the generator is sending: the generator's admin port (ADR 0002)."""

import json
import logging
import math
import socket
from dataclasses import replace
from typing import Protocol, TypeIs

from simulators.common.faults import (
    EXTRA_CABLE_LOSS,
    LINK_DOWN,
    LINK_FAILS_EVERY,
    FaultSwitches,
)
from simulators.common.physics import SourceState

NO_SIGNAL = SourceState(frequency_hz=1e9, level_dbm=-200.0, output_on=False)
"""What the analyzer sees when it can't reach the generator: nothing, like an unplugged cable."""

_LOGGER = logging.getLogger(__name__)

MAX_REPLY_CHARACTERS = 4096
"""A state reply is under 100 characters; anything past this is cut off, then rejected."""


class GeneratorLink(Protocol):
    """Anything that can say what the generator is sending right now."""

    def source_state(self) -> SourceState:
        """Report the generator's current output.

        Returns:
            The generator's frequency, level and RF output state.
        """


def parse_state(reply: str) -> SourceState:
    """Read the generator's STATE? reply.

    Args:
        reply: One line of JSON from the generator's admin port.

    Returns:
        The state it describes.

    Raises:
        ValueError: If the reply isn't JSON or doesn't hold a valid state: all three fields,
            finite numbers and a positive frequency. Overflowing numbers and over-nested JSON
            are reported the same way.
    """
    try:
        data = json.loads(reply)
    except RecursionError as error:
        raise ValueError("Generator state is nested too deeply to be a state") from error
    if not isinstance(data, dict):
        raise ValueError(f"Generator state isn't a JSON object: {reply.strip()[:80]!r}")
    frequency_hz, level_dbm, output_on = (
        data.get("frequency_hz"),
        data.get("level_dbm"),
        data.get("output_on"),
    )
    if not (_is_number(frequency_hz) and _is_number(level_dbm) and isinstance(output_on, bool)):
        raise ValueError(f"Generator state has missing or wrong fields: {reply.strip()[:80]!r}")
    try:
        state = SourceState(float(frequency_hz), float(level_dbm), output_on)
    except OverflowError as error:
        raise ValueError(f"Generator state has a number too large: {reply[:80]!r}") from error
    if not (math.isfinite(state.frequency_hz) and math.isfinite(state.level_dbm)):
        raise ValueError(f"Generator state has a non-finite number: {reply.strip()[:80]!r}")
    if state.frequency_hz <= 0:
        raise ValueError(f"Generator frequency must be positive: {reply.strip()[:80]!r}")
    return state


def _is_number(value: object) -> TypeIs[int | float]:
    """Check that a JSON value is a number, not a bool (which Python counts as an int).

    Args:
        value: The value to check.

    Returns:
        True for an int or float that isn't a bool.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class AdminPortLink:
    """Asks the generator's admin port for its state, once per measurement.

    The call blocks while it waits. The line server runs it in a worker thread, so the
    generator can be another process or share the analyzer's event loop.
    """

    def __init__(self, host: str, port: int, timeout_s: float = 1.0) -> None:
        """Remember where the generator's admin port is.

        Args:
            host: The generator's address.
            port: The generator's admin port.
            timeout_s: How long to wait for a connection and a reply.
        """
        self.host = host
        self.port = port
        self.timeout_s = timeout_s

    def source_state(self) -> SourceState:
        """Ask the generator what it is sending.

        Returns:
            The generator's state, or NO_SIGNAL if it can't be reached or answers nonsense;
            that case is logged as a warning.
        """
        try:
            with socket.create_connection((self.host, self.port), self.timeout_s) as connection:
                connection.sendall(b"STATE?\n")
                reply = connection.makefile("r", encoding="ascii").readline(MAX_REPLY_CHARACTERS)
            return parse_state(reply)
        except (OSError, ValueError) as error:
            _LOGGER.warning(
                "Generator link unavailable; measuring no signal",
                extra={"generator_admin": f"{self.host}:{self.port}", "reason": str(error)},
            )
            return NO_SIGNAL


class CableWithFaults:
    """The cable between generator and analyzer, with any extra loss the cable fault adds."""

    def __init__(self, link: GeneratorLink, switches: FaultSwitches) -> None:
        """Wrap the link to the generator.

        Args:
            link: How the analyzer reads the generator's state.
            switches: The analyzer's fault switches.
        """
        self._link = link
        self._switches = switches
        self._readings = 0

    def source_state(self) -> SourceState:
        """Report the generator's state as it arrives at the analyzer.

        Returns:
            The generator's state with its level lowered by any extra cable loss, or no
            signal at all when the cable is disconnected or this reading is one the loose
            connector drops.
        """
        self._readings += 1
        if self._cable_is_disconnected():
            _LOGGER.warning(
                "No signal reaching the analyzer: nothing is connected",
                extra={"reading": self._readings},
            )
            return NO_SIGNAL
        state = self._link.source_state()
        extra_loss_db = self._switches.value(EXTRA_CABLE_LOSS.name)
        if not extra_loss_db:
            return state
        return replace(state, level_dbm=state.level_dbm - extra_loss_db)

    def _cable_is_disconnected(self) -> bool:
        """Check whether this reading sees a disconnected cable.

        Returns:
            True when the cable is down, or when a loose connector drops this reading.
        """
        if self._switches.value(LINK_DOWN.name):
            return True
        every = int(self._switches.value(LINK_FAILS_EVERY.name))
        return every >= 1 and self._readings % every == 0
