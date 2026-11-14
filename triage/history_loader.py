"""The background loop that loads the persistent history and keeps it current.

It runs beside the server, so the process starts and answers its liveness probe while the
history loads. A failed load (usually Ollama down with vectors missing from the cache) is
retried with backoff: 2 s, doubling to 60 s. Once loaded, it waits for one of two things: a
rebuild request (the index went stale after a failed update) or the time to check the
embedding model's digest again. A changed digest makes the history not ready and starts a
rebuild under the new digest. Waiting goes through an injected function, so tests drive
the loop without sleeping.
"""

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from triage.case import CaseError
from triage.history_store import HistoryUnavailableError, PersistentHistory
from triage.llm import ModelUnavailableError

BACKOFF_START_S = 2.0
"""The first wait after a failed load or an unanswered digest check."""

BACKOFF_MAX_S = 60.0
"""The longest wait between attempts."""

VERIFY_INTERVAL_S = 300.0
"""How often a verified digest is checked again."""

Wait = Callable[[float], bool]
"""Waits up to the given seconds; True when woken early by a rebuild request or a stop."""


def next_delay(delay_s: float) -> float:
    """Double a retry delay, up to the cap.

    Args:
        delay_s: The delay just used.

    Returns:
        The next one.
    """
    return min(delay_s * 2, BACKOFF_MAX_S)


@dataclass
class HistoryLoader:
    """Loads a persistent history and rebuilds it when it goes stale or its model changes.

    Attributes:
        history: The history to load.
        stop: Set to end the loop.
        wait: How the loop waits; None waits on the history's rebuild event.
    """

    history: PersistentHistory
    stop: threading.Event = field(default_factory=threading.Event)
    wait: Wait | None = None

    def attempt(self) -> bool:
        """Try one load; a failure is recorded for readiness to report.

        Returns:
            True when the new index was swapped in.
        """
        self.history.rebuild.clear()
        try:
            self.history.load()
        except (
            ModelUnavailableError,
            CaseError,
            OSError,
            sqlite3.Error,
            HistoryUnavailableError,
        ) as error:
            self.history.wait_reason(str(error))
            return False
        return True

    def run(self) -> None:
        """Load, retrying with backoff, then keep the history current until stopped."""
        if self.history.open_error is not None:
            return
        delay_s = BACKOFF_START_S
        while not self.stop.is_set():
            if self.attempt():
                delay_s = BACKOFF_START_S
                self.watch()
            else:
                self._wait(delay_s)
                delay_s = next_delay(delay_s)

    def watch(self) -> None:
        """Wait until a rebuild is needed: a rebuild request, a changed digest, or a stop."""
        delay_s = BACKOFF_START_S
        while not self.stop.is_set():
            verified = self.history.status().digest_verified
            woken = self._wait(VERIFY_INTERVAL_S if verified else delay_s)
            if woken or self.history.rebuild.is_set() or self.stop.is_set():
                return
            if self.history.verify_digest():
                return
            delay_s = BACKOFF_START_S if verified else next_delay(delay_s)

    def _wait(self, seconds: float) -> bool:
        """Wait through the injected function, or on the history's rebuild event.

        Args:
            seconds: The longest wait.

        Returns:
            True when woken early.
        """
        if self.wait is not None:
            return self.wait(seconds)
        return self.history.rebuild.wait(seconds)


def start_in_background(loader: HistoryLoader) -> threading.Thread:
    """Run a loader on a daemon thread, so it never keeps the process alive.

    Args:
        loader: The loader.

    Returns:
        The started thread.
    """
    thread = threading.Thread(target=loader.run, name="history-loader", daemon=True)
    thread.start()
    return thread
