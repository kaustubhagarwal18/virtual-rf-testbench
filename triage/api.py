"""The triage service over HTTP: post a failed run, get back what to do about it.

Three endpoints, around the same pipeline `make triage` and the evaluation use:

- `POST /triage` takes a run's evidence (the results file the bench suite writes, and
  optionally the instrument logs and the sequence log) and returns the verdict.
- `POST /history` takes a resolved run, with its label, into the history retrieval searches,
  so the next similar failure is triaged with it as a precedent.
- `GET /health` says whether the service is up and how much history it holds.

Everything a request carries is untrusted data: it goes into the prompt as evidence, never
as instructions, and the model has no tools. The service listens on localhost by default and
talks only to the local Ollama; nothing leaves the machine.

`make triage-api` starts it.
"""

import argparse
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from triage.abstain import SERVICE_GATES, SourceGate
from triage.answer import VERDICT_SCHEMA, MalformedAnswerError
from triage.checks import DEFAULT_CHECKS, VerdictCheck
from triage.evidence import SavedRun
from triage.llm import DEFAULT_MODEL, LLMProvider, ModelUnavailableError, OllamaProvider
from triage.retrieval import (
    DEFAULT_HISTORY,
    DEFAULT_TOP_K,
    HistoryStore,
    InMemoryRetriever,
    Retriever,
    history_index,
)
from triage.service import triage_run

MAX_LINES = 2000
"""The most log or sequence lines one request may carry: a run's logs are kept to far fewer."""

MAX_TESTS = 500
"""The most tests one results file may list: the bench suite has 20."""

MAX_LINE_LENGTH = 20000
"""The longest log or sequence line: an instrument's JSON log line is a few hundred characters."""

MAX_MEASUREMENTS = 200
"""The most measurements one test may carry: a calibration test records a handful."""

MAX_INSTRUMENTS = 20
"""The most entries in a per-instrument or per-outcome map: the bench has two instruments."""

MAX_TEXT = 2000
"""The longest label field: a cause or a fix is a sentence or two."""

REQUEST_FOLDER = Path("request")
"""The folder a posted run is given: it was never saved, so it has none of its own."""

DEFAULT_PORT = 8000

ActionKey = Literal[
    "CABLE", "LINK", "ALIGNMENT", "CONFIGURATION", "TIMEOUT", "TEST_CLEANUP", "FIRMWARE", "UNKNOWN"
]
"""The action keys a label may record: triage.prompt.Fix's names, spelled as a type."""

EvidenceLevel = Literal["full", "error-only"]
"""The evidence levels a request may ask for: triage.evidence.LEVELS, spelled as a type."""


class SearchableHistory(Retriever, HistoryStore, Protocol):
    """A history that can be searched, added to, and counted."""

    def __len__(self) -> int:
        """Count the past runs it holds.

        Returns:
            The count.
        """


Reading = float | str | None
"""A recorded number: a float, or 'NaN', 'Infinity' or '-Infinity' for a non-finite reading,
as the measurement records write them; None where a one-sided limit has no bound."""

Line = Annotated[str, Field(max_length=MAX_LINE_LENGTH)]
"""One log or sequence line, capped so a single line can't carry an unbounded payload."""


class Measurement(BaseModel):
    """One measurement of a test: every field the evidence builder reads is typed.

    Attributes:
        name: What was measured.
        value: The reading.
        unit: Its unit.
        limit_low: The lower limit, if any.
        limit_high: The upper limit, if any.
        margin: How far inside (positive) or outside (negative) the limits it was.
        passed: Whether it was within the limits.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(max_length=200)
    value: Reading
    unit: str = Field(default="", max_length=20)
    limit_low: Reading = None
    limit_high: Reading = None
    margin: Reading = None
    passed: bool


class TestResult(BaseModel):
    """One test in a results file: what the evidence builder reads is typed; the rest is kept.

    Attributes:
        id: The test's pytest id.
        outcome: passed, failed, error or skipped.
        message: The failure message, if any.
        measurements: Its measurements, with limits and margins.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(max_length=500)
    outcome: str = Field(max_length=20)
    message: str = Field(default="", max_length=20000)
    measurements: list[Measurement] = Field(default_factory=list, max_length=MAX_MEASUREMENTS)


class Selection(BaseModel):
    """Which tests the run collected and selected, so a subset never passes for a full run.

    Attributes:
        collected: How many tests pytest collected.
        selected: How many it ran.
        deselected: How many it left out.
    """

    model_config = ConfigDict(extra="allow")

    collected: int = Field(ge=0)
    selected: int = Field(ge=0)
    deselected: int = Field(ge=0)


class Results(BaseModel):
    """A bench run's results file, as the bench suite writes it.

    Every field the evidence builder reads has a type, so a malformed one is refused with a
    422 before the model is asked, rather than failing inside the pipeline with a 500.
    Fields it doesn't read are kept as sent.

    Attributes:
        tests: Every test, with its outcome and message.
        firmware_versions: Each instrument's release; empty when not recorded, which the
            evidence reports as unknown.
        selection: Which tests ran; None when not recorded.
        totals: How many tests passed, failed, errored and were skipped.
    """

    model_config = ConfigDict(extra="allow")

    tests: list[TestResult] = Field(max_length=MAX_TESTS)
    firmware_versions: dict[str, Annotated[str, Field(max_length=100)]] = Field(
        default_factory=dict, max_length=MAX_INSTRUMENTS
    )
    selection: Selection | None = None
    totals: dict[str, int] = Field(default_factory=dict, max_length=MAX_INSTRUMENTS)


class Evidence(BaseModel):
    """A run's evidence, as a request carries it.

    Attributes:
        results: The results file.
        logs: The instruments' log lines during the run.
        sequence: The setup sequence's log lines.
    """

    results: Results
    logs: list[Line] = Field(default_factory=list, max_length=MAX_LINES)
    sequence: list[Line] = Field(default_factory=list, max_length=MAX_LINES)

    def saved_run(self) -> SavedRun:
        """Turn the request's evidence into the run the pipeline reads.

        Returns:
            The run, with the request's evidence and no folder of its own.
        """
        return SavedRun(
            folder=REQUEST_FOLDER,
            results=self.results.model_dump(),
            logs=list(self.logs),
            sequence=list(self.sequence),
        )


class RunRequest(Evidence):
    """A failed run to triage.

    Attributes:
        level: How much of the evidence the model is given.
        retrieve: Whether to offer the most similar past runs as sources.
    """

    level: EvidenceLevel = "full"
    retrieve: bool = True


class Label(BaseModel):
    """How a past run was resolved.

    Attributes:
        fault: What was wrong, as a short name.
        cause: Why it failed.
        fix: What was done about it.
        action: The key of the action taken, such as LINK; optional, but a past run that
            records it gives the model the action itself, not only a description of it.
    """

    fault: str = Field(min_length=1, max_length=100)
    cause: str = Field(min_length=1, max_length=MAX_TEXT)
    fix: str = Field(min_length=1, max_length=MAX_TEXT)
    action: ActionKey | None = None


class ResolvedRunRequest(Evidence):
    """A resolved run to add to the history.

    Attributes:
        label: How it was resolved.
    """

    label: Label


class PastFixResponse(BaseModel):
    """What fixed a cited past run, quoted from its record.

    Attributes:
        source_id: The cited run's neutral id.
        fix_applied: Its recorded fix, word for word.
    """

    source_id: str
    fix_applied: str


class VerdictResponse(BaseModel):
    """What to do about a failed run.

    Attributes:
        fix: The chosen action's key, such as CABLE.
        action: What the action means, in words.
        where: What to act on.
        because: The one-sentence reason.
        cited: The past runs it leaned on, by their neutral ids.
        overruled: Why a check replaced the model's choice, or empty.
        gate_reason: Why UNKNOWN was answered from the past runs alone, without asking the
            model, or empty when the model was asked.
        category: Whether the action answers the firmware, the bench or the test code, or
            unknown for UNKNOWN.
        past_fixes: What fixed each cited past run, quoted from its record, in citation order.
    """

    fix: str
    action: str
    where: str
    because: str
    cited: list[str]
    overruled: str
    gate_reason: str
    category: str
    past_fixes: list[PastFixResponse]


class AddedResponse(BaseModel):
    """A run added to the history.

    Attributes:
        source_id: The neutral id it will be cited by.
        history_size: How many past runs the history holds now.
    """

    source_id: str
    history_size: int


class HealthResponse(BaseModel):
    """Whether the service is up.

    Attributes:
        status: Always "ok" when it answers.
        retrieval: Whether a history is loaded to retrieve from.
        history_size: How many past runs it holds.
    """

    status: Literal["ok"]
    retrieval: bool
    history_size: int


def create_app(
    provider: LLMProvider,
    history: SearchableHistory | None = None,
    checks: tuple[VerdictCheck, ...] = DEFAULT_CHECKS,
    top_k: int = DEFAULT_TOP_K,
    gates: tuple[SourceGate, ...] = SERVICE_GATES,
) -> FastAPI:
    """Build the service around an injected model and history, so tests can pass fakes.

    Args:
        provider: The answering model.
        history: The searchable history; None runs without retrieval.
        checks: The checks every verdict goes through.
        top_k: How many past runs to offer with each question.
        gates: The gates the retrieved past runs go through before the model is asked.

    Returns:
        The application, ready for uvicorn or a test client.
    """
    app = FastAPI(
        title="virtual-rf-testbench triage",
        description="Is a failed bench run the firmware, the bench or the test?",
        version="0.2.0",
    )
    # The in-memory history is shared between request threads, so it is used under a lock.
    lock = threading.Lock()

    @app.get("/health")
    def health() -> HealthResponse:
        """Say whether the service is up and how much history it holds.

        Returns:
            The service's status.
        """
        return HealthResponse(
            status="ok",
            retrieval=history is not None,
            history_size=len(history) if history is not None else 0,
        )

    @app.post("/triage")
    def triage(request: RunRequest) -> VerdictResponse:
        """Triage one failed run.

        Args:
            request: The run's evidence, and how to ask about it.

        Returns:
            The verdict.

        Raises:
            HTTPException: 503 when the model can't be reached, 502 when its answer can't be
                used; the detail is one line saying what is wrong.
        """
        run = request.saved_run()
        sources = []
        try:
            if request.retrieve and history is not None:
                with lock:
                    sources = history.similar_to(run, top_k)
            verdict = triage_run(run, provider, request.level, sources, checks=checks, gates=gates)
        except ModelUnavailableError as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
        except MalformedAnswerError as error:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"the model's answer couldn't be used: {error}"
            ) from error
        return VerdictResponse(
            fix=verdict.fix.name,
            action=verdict.fix.value,
            where=verdict.where,
            because=verdict.because,
            cited=verdict.cited,
            overruled=verdict.overruled,
            gate_reason=verdict.gate_reason,
            category=verdict.category,
            past_fixes=[
                PastFixResponse(source_id=past.source_id, fix_applied=past.fix_applied)
                for past in verdict.past_fixes
            ],
        )

    @app.post("/history", status_code=status.HTTP_201_CREATED)
    def add_history(request: ResolvedRunRequest) -> AddedResponse:
        """Add a resolved run to the history, so later failures can cite it.

        Args:
            request: The run's evidence and how it was resolved.

        Returns:
            The id it will be cited by, and the history's new size.

        Raises:
            HTTPException: 409 when the service runs without a history, 503 when the
                embedding model can't be reached.
        """
        if history is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "this service runs without a history; restart it without --no-history",
            )
        try:
            with lock:
                source = history.add(request.saved_run(), request.label.model_dump())
                size = len(history)
        except ModelUnavailableError as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
        return AddedResponse(source_id=source.source_id, history_size=size)

    return app


def _parser() -> argparse.ArgumentParser:
    """Build the command line's parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="python -m triage.api", description="Serve triage over HTTP on this machine."
    )
    parser.add_argument("--host", default="127.0.0.1", help="where to listen (default: localhost)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="the port to listen on")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="history runs")
    parser.add_argument(
        "--no-history", action="store_true", help="run without retrieval or a history"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Ollama model to ask (default: {DEFAULT_MODEL})"
    )
    return parser


def main(arguments: Sequence[str] | None = None, serve: Any = None) -> int:
    """Load the history and serve the API until stopped.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        serve: What runs the app, given the app, host and port; None uses uvicorn.run.

    Returns:
        0 when the server stopped, 1 if the history couldn't be loaded.
    """
    parsed = _parser().parse_args(arguments)
    history: InMemoryRetriever | None = None
    if not parsed.no_history:
        if not parsed.history.is_dir():
            sys.stdout.write(
                f"no history at {parsed.history}; build it with 'make fault-dataset', or "
                "start without one with --no-history\n"
            )
            return 1
        try:
            history = history_index(parsed.history)
        except ModelUnavailableError as error:
            sys.stdout.write(f"{error}\n")
            return 1
    provider = OllamaProvider(model=parsed.model, response_format=VERDICT_SCHEMA)
    serve = serve or uvicorn.run
    serve(create_app(provider, history), host=parsed.host, port=parsed.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
