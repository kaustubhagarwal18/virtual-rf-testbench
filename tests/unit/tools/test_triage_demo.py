"""Tests for the retrieval demo: the same run, triaged with and without past runs."""

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from tools.triage_demo import UNUSABLE, main, render, run_demo
from triage.bench_run import load_case
from triage.case import Case
from triage.llm import FakeProvider, LLMProvider, ModelUnavailableError
from triage.pack import DEFAULT_PACK
from triage.prompt import Source
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
    """A retriever that always offers the same past runs, for an offline demo test."""

    def __init__(self, *sources: Source) -> None:
        """Keep the sources this retriever offers.

        Args:
            sources: The past runs to offer every time, most similar first.
        """
        self.sources = sources

    def similar_to(self, run: Case, count: int, exclude_fault: str | None = None) -> list[Source]:
        """Return the fixed sources, up to the count asked for.

        Args:
            run: The run being triaged (unused).
            count: How many to return at most.
            exclude_fault: A fault to leave out; sources with it are dropped.

        Returns:
            The fixed sources without the excluded fault, at most count of them.
        """
        del run
        kept = [source for source in self.sources if source.fault != exclude_fault]
        return kept[:count]


class UnreachableProvider:
    """A provider that is never reachable, for the error path."""

    def complete(self, system: str, user: str, schema: object = None) -> str:
        """Fail the way an unreachable model does.

        Args:
            system: The instructions (unused).
            user: The question (unused).
            schema: The reply's schema (unused).

        Returns:
            Never; it always raises.

        Raises:
            ModelUnavailableError: Always, naming how to start the model.
        """
        del system, user, schema
        raise ModelUnavailableError("start it with 'ollama serve'")


def cable_run(tmp_path: Path) -> Case:
    """Return a saved cable_loss question run, so its resolving fix is CABLE."""
    folder = write_run(tmp_path / "cable_loss", [FAILING_TEST])
    (folder / "label.json").write_text(
        json.dumps(
            {
                "fault": "cable_loss",
                "category": "bench",
                "cause": "worn cable",
                "fix": "x",
                "action": "CABLE",
            }
        ),
        encoding="utf-8",
    )
    return load_case(folder)


def demo_retriever() -> Retriever:
    """Return a retriever that offers the matching cable_loss precedent."""
    return StubRetriever(Source("cable_loss-1", "path loss high; cause: bench; fix: new cable"))


def test_retrieval_flips_a_wrong_baseline_to_the_resolving_fix(tmp_path: Path) -> None:
    """Without past runs the model blames firmware; with them it chooses the cable, and cites it."""
    provider = FakeProvider([FIRMWARE_REPLY, CABLE_REPLY])
    result = run_demo(cable_run(tmp_path), provider, demo_retriever())
    assert result.expected is DEFAULT_PACK.action("CABLE")
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


@pytest.mark.parametrize(
    ("replies", "sentence"),
    [
        (
            [FIRMWARE_REPLY, CABLE_REPLY],
            "Retrieval changed the verdict from FIRMWARE to CABLE, the resolving fix.",
        ),
        (
            [CABLE_REPLY_NO_CITE, FIRMWARE_REPLY],
            "Retrieval changed the verdict from CABLE to FIRMWARE, not the resolving fix.",
        ),
    ],
)
def test_render_names_the_change_retrieval_made(
    tmp_path: Path, replies: list[str], sentence: str
) -> None:
    """The report marks each verdict and says whether retrieval moved it to the resolving fix."""
    report = render(run_demo(cable_run(tmp_path), FakeProvider(replies), demo_retriever()))
    assert "[wrong]" in report
    assert "[RIGHT]" in report
    assert sentence in report


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


def _no_results(tmp_path: Path) -> Path:
    """A folder that is not a saved run: it has no results.json."""
    folder = tmp_path / "empty"
    folder.mkdir()
    return folder


def _no_label(tmp_path: Path) -> Path:
    """A saved run whose label.json is missing."""
    run = write_run(tmp_path / "run", [FAILING_TEST])
    (run / "label.json").unlink()
    return run


def _unknown_fault(tmp_path: Path) -> Path:
    """A saved run whose label names a fault with no known fix."""
    run = write_run(tmp_path / "run", [FAILING_TEST])
    (run / "label.json").write_text(json.dumps({"fault": "no_such_fault"}), encoding="utf-8")
    return run


def _no_fault_key(tmp_path: Path) -> Path:
    """A saved run whose label has no fault key at all."""
    run = write_run(tmp_path / "run", [FAILING_TEST])
    (run / "label.json").write_text(json.dumps({"category": "bench"}), encoding="utf-8")
    return run


@pytest.mark.parametrize("make_run", [_no_results, _no_label, _unknown_fault, _no_fault_key])
def test_main_reports_bad_input_without_a_traceback(
    make_run: Callable[[Path], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing run, label or fault key, or an unknown fault, exits 1 with one line."""
    exit_code = main([str(make_run(tmp_path))], FakeProvider([FIRMWARE_REPLY]), demo_retriever())
    out = capsys.readouterr().out
    assert exit_code == 1
    assert out.strip()
    assert "\n" not in out.strip()


def test_demo_runs_as_a_module() -> None:
    """Python -m tools.triage_demo works the way make triage-demo calls it."""
    completed = subprocess.run(
        [sys.executable, "-m", "tools.triage_demo", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "what retrieval adds" in completed.stdout


def test_the_report_states_the_cause_and_what_fixed_the_cited_run(tmp_path: Path) -> None:
    """The retrieval answer says bench, and quotes the cited past run's recorded fix."""
    source = Source("cable_loss-1", "path loss high", fix_applied="Replaced the cable")
    provider = FakeProvider([FIRMWARE_REPLY, CABLE_REPLY])
    result = run_demo(cable_run(tmp_path), provider, StubRetriever(source))
    assert (result.baseline.category, result.retrieval.category) == ("firmware", "bench")
    report = render(result)
    assert "cause: bench" in report
    assert "last time: Replaced the cable (cable_loss-1)" in report


def test_the_report_says_which_cited_run_each_fix_came_from(tmp_path: Path) -> None:
    """Two cited runs fixed different ways: each fix is printed beside the run that recorded it."""
    sources = [
        Source("past-a", "a", fix_applied="Replaced the cable"),
        Source("past-b", "b", fix_applied="Reseated the connector"),
    ]

    reply = json.dumps(
        {"fix": "CABLE", "where": "cable", "because": "x", "cited": ["past-a", "past-b"]}
    )
    provider = FakeProvider([FIRMWARE_REPLY, reply])
    report = render(run_demo(cable_run(tmp_path), provider, StubRetriever(*sources)))
    assert "last time: Replaced the cable (past-a)" in report
    assert "last time: Reseated the connector (past-b)" in report


def test_main_reports_a_malformed_case_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A case.json that isn't JSON is one line naming the file and exit 1, not a traceback."""
    (tmp_path / "case.json").write_text("{", encoding="utf-8")
    assert main([str(tmp_path)], FakeProvider([]), demo_retriever()) == 1
    output = capsys.readouterr().out
    assert "case.json" in output
    assert len(output.strip().splitlines()) == 1
