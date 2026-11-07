"""Tests for the retrieval demo: the same run, triaged with and without past runs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from tools.triage_demo import UNUSABLE, main, render, run_demo
from triage.evidence import SavedRun
from triage.llm import FakeProvider, LLMProvider, ModelUnavailableError
from triage.prompt import Fix, Source
from triage.retrieval import Retriever

FIRMWARE_REPLY = json.dumps(
    {"fix": "FIRMWARE", "where": "the generator firmware", "because": "levels are off", "cited": []}
)
CABLE_REPLY = json.dumps(
    {
        "fix": "CABLE",
        "where": "the bench cable",
        "because": "the same loss resolved by a new cable before",
        "cited": ["cable_loss-1"],
    }
)
CABLE_REPLY_NO_CITE = json.dumps(
    {"fix": "CABLE", "where": "the bench cable", "because": "the loss is high", "cited": []}
)


class StubRetriever:
    """A retriever that always offers one fixed past run, for an offline demo test."""

    def __init__(self, source: Source) -> None:
        """Keep the single source this retriever offers.

        Args:
            source: The past run to offer every time.
        """
        self.source = source

    def similar_to(self, run: SavedRun, count: int) -> list[Source]:
        """Return the fixed source, up to the count asked for.

        Args:
            run: The run being triaged (unused).
            count: How many to return at most.

        Returns:
            The single fixed source, or none when count is zero.
        """
        del run
        return [self.source][:count]


class UnreachableProvider:
    """A provider that is never reachable, for the error path."""

    def complete(self, system: str, user: str) -> str:
        """Fail the way an unreachable model does.

        Args:
            system: The instructions (unused).
            user: The question (unused).

        Returns:
            Never; it always raises.

        Raises:
            ModelUnavailableError: Always, naming how to start the model.
        """
        del system, user
        raise ModelUnavailableError("start it with 'ollama serve'")


def cable_run(tmp_path: Path) -> SavedRun:
    """Return a saved cable_loss question run, so its resolving fix is CABLE."""
    folder = write_run(tmp_path / "cable_loss", [FAILING_TEST])
    (folder / "label.json").write_text(
        json.dumps({"fault": "cable_loss", "category": "bench", "cause": "worn cable", "fix": "x"}),
        encoding="utf-8",
    )
    return SavedRun.load(folder)


def demo_retriever() -> Retriever:
    """Return a retriever that offers the matching cable_loss precedent."""
    return StubRetriever(Source("cable_loss-1", "path loss high; cause: bench; fix: new cable"))


def test_retrieval_flips_a_wrong_baseline_to_the_resolving_fix(tmp_path: Path) -> None:
    """Without past runs the model blames firmware; with them it chooses the cable, and cites it."""
    provider = FakeProvider([FIRMWARE_REPLY, CABLE_REPLY])
    result = run_demo(cable_run(tmp_path), provider, demo_retriever())
    assert result.expected is Fix.CABLE
    assert result.baseline.chosen == "FIRMWARE"
    assert not result.baseline.right
    assert result.retrieval.chosen == "CABLE"
    assert result.retrieval.right
    assert result.retrieval.cited == ("cable_loss-1",)


def test_both_answers_see_the_same_evidence_so_only_sources_differ(tmp_path: Path) -> None:
    """The baseline prompt carries no SOURCES block; the retrieval prompt names the precedent."""
    provider = FakeProvider([FIRMWARE_REPLY, CABLE_REPLY])
    run_demo(cable_run(tmp_path), provider, demo_retriever())
    baseline_prompt, retrieval_prompt = provider.prompts[0][1], provider.prompts[1][1]
    assert "cable_loss-1" not in baseline_prompt
    assert "[cable_loss-1]" in retrieval_prompt


def test_an_unusable_reply_is_shown_as_a_result_not_a_crash(tmp_path: Path) -> None:
    """A reply that can't be parsed becomes an UNUSABLE answer, counted as wrong."""
    provider = FakeProvider([FIRMWARE_REPLY, "sorry, no JSON here"])
    result = run_demo(cable_run(tmp_path), provider, demo_retriever())
    assert result.retrieval.chosen == UNUSABLE
    assert not result.retrieval.right


def test_render_names_the_change_retrieval_made(tmp_path: Path) -> None:
    """The report marks each verdict and states the action retrieval changed it to."""
    provider = FakeProvider([FIRMWARE_REPLY, CABLE_REPLY])
    report = render(run_demo(cable_run(tmp_path), provider, demo_retriever()))
    assert "[wrong]" in report
    assert "[RIGHT]" in report
    assert "changed the verdict from FIRMWARE to CABLE to the resolving fix" in report


def test_render_says_when_the_verdict_did_not_change(tmp_path: Path) -> None:
    """When both answers agree, the report says retrieval did not change the verdict."""
    provider = FakeProvider([CABLE_REPLY_NO_CITE, CABLE_REPLY_NO_CITE])
    report = render(run_demo(cable_run(tmp_path), provider, demo_retriever()))
    assert "did not change the verdict (CABLE)" in report


def test_main_prints_the_report_with_injected_dependencies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Make triage-demo RUN=... prints both verdicts and exits 0."""
    provider: LLMProvider = FakeProvider([FIRMWARE_REPLY, CABLE_REPLY])
    assert main([str(cable_run(tmp_path).folder)], provider, demo_retriever()) == 0
    out = capsys.readouterr().out
    assert "Without retrieval" in out
    assert "With retrieval" in out


def test_main_reports_an_unreachable_model_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A model that can't be reached exits 1 with one line, not a stack trace."""
    assert main([str(cable_run(tmp_path).folder)], UnreachableProvider(), demo_retriever()) == 1
    assert "ollama serve" in capsys.readouterr().out


def test_a_long_reason_is_clipped_in_the_report(tmp_path: Path) -> None:
    """A verbose reason is shortened with an ellipsis so the report stays readable."""
    long_reason = "the loss is far too high " * 20
    reply = json.dumps({"fix": "FIRMWARE", "where": "x", "because": long_reason, "cited": []})
    report = render(run_demo(cable_run(tmp_path), FakeProvider([reply]), demo_retriever()))
    assert "..." in report


def test_demo_runs_as_a_module() -> None:
    """Python -m tools.triage_demo works the way make triage-demo calls it."""
    completed = subprocess.run(
        [sys.executable, "-m", "tools.triage_demo", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "what retrieval adds" in completed.stdout
