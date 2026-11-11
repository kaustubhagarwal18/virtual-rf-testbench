"""Tests for the pipeline: evidence, prompt, answer."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from triage.abstain import DEFAULT_GATES
from triage.answer import MalformedAnswerError, PastFix, Verdict
from triage.checks import DEFAULT_CHECKS
from triage.evidence import ERROR_ONLY, FULL, SavedRun
from triage.llm import FakeProvider, ModelUnavailableError
from triage.pack import DEFAULT_PACK, UNKNOWN
from triage.prompt import Source
from triage.service import describe, main, triage_run

VERDICT = json.dumps(
    {"fix": "CABLE", "where": "the bench cable", "because": "the loss is high", "cited": []}
)


def saved_run(tmp_path: Path) -> SavedRun:
    """Return a saved run with one failing measurement."""
    return SavedRun.load(write_run(tmp_path / "run", [FAILING_TEST]))


def test_the_chosen_action_comes_back_as_the_verdict(tmp_path: Path) -> None:
    """A well-formed reply becomes the verdict the caller acts on."""
    verdict = triage_run(saved_run(tmp_path), FakeProvider([VERDICT]))
    assert verdict.fix is DEFAULT_PACK.action("CABLE")
    assert verdict.where == "the bench cable"


def test_the_evidence_level_decides_what_the_model_is_told(tmp_path: Path) -> None:
    """The error-only scenario sees the message; the full one sees the firmware too."""
    run = saved_run(tmp_path)
    bare, full = FakeProvider([VERDICT]), FakeProvider([VERDICT])
    triage_run(run, bare, ERROR_ONLY)
    triage_run(run, full, FULL)
    assert "Firmware" not in bare.prompts[0][1]
    assert "Firmware" in full.prompts[0][1]


def test_sources_reach_the_prompt_and_may_be_cited(tmp_path: Path) -> None:
    """With history offered, the model sees it and a citation to it is accepted."""
    sources = [Source("cable-loss-03", "path loss 1.9 dB; cause: bench; fixed: new cable")]
    reply = json.dumps(
        {"fix": "CABLE", "where": "cable", "because": "same as before", "cited": ["cable-loss-03"]}
    )
    provider = FakeProvider([reply])
    verdict = triage_run(saved_run(tmp_path), provider, FULL, sources)
    assert "[cable-loss-03]" in provider.prompts[0][1]
    assert verdict.cited == ["cable-loss-03"]


def test_a_citation_that_was_never_offered_is_refused(tmp_path: Path) -> None:
    """Without sources, any citation is invented, and the run counts as unusable."""
    reply = json.dumps({"fix": "CABLE", "where": "cable", "because": "x", "cited": ["run-42"]})
    with pytest.raises(MalformedAnswerError, match="cites sources it wasn't given"):
        triage_run(saved_run(tmp_path), FakeProvider([reply]))


def test_a_malformed_reply_is_not_retried(tmp_path: Path) -> None:
    """How often the model answers unusably is a result, so the pipeline asks only once."""
    provider = FakeProvider(["sorry, I can't help with that"])
    with pytest.raises(MalformedAnswerError):
        triage_run(saved_run(tmp_path), provider)
    assert len(provider.prompts) == 1


def test_the_cli_prints_the_verdict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Make triage RUN=... prints the chosen fix, where, why and what it cited."""
    run = write_run(tmp_path / "run", [FAILING_TEST])
    reply = json.dumps({"fix": "CABLE", "where": "the cable", "because": "loss high", "cited": []})
    assert main([str(run)], FakeProvider([reply])) == 0
    out = capsys.readouterr().out
    assert "fix:   CABLE" in out
    assert "cited: nothing" in out


def test_the_cli_reports_an_unusable_answer_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reply that can't be parsed exits 1 with one line, not a stack trace."""
    run = write_run(tmp_path / "run", [FAILING_TEST])
    assert main([str(run)], FakeProvider(["not json"])) == 1
    assert "couldn't be used" in capsys.readouterr().out


def test_the_cli_reports_an_unreachable_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A model that can't be reached exits 1 with one line, not a traceback."""

    class Down:
        """A provider that is never reachable."""

        def complete(self, system: str, user: str, schema: object = None) -> str:
            """Fail as an unreachable model does."""
            raise ModelUnavailableError("start it with 'ollama serve'")

    run = write_run(tmp_path / "run", [FAILING_TEST])
    assert main([str(run)], Down()) == 1
    assert "ollama serve" in capsys.readouterr().out


def test_service_runs_as_a_module() -> None:
    """Python -m triage.service works the way make triage calls it."""
    completed = subprocess.run(
        [sys.executable, "-m", "triage.service", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Explain one failed bench run" in completed.stdout


def unchanged_firmware_run(tmp_path: Path) -> Path:
    """Write a failed run whose firmware is the same release as the last passing run's."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    results["firmware_versions"] = {"analyzer": "0.1.0", "generator": "0.1.0"}
    (folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
    return folder


FIRMWARE_REPLY = json.dumps({"fix": "FIRMWARE", "where": "fw", "because": "x", "cited": []})


def test_the_cli_says_when_a_check_overruled_the_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A FIRMWARE answer on unchanged firmware prints UNKNOWN and the reason it was overruled."""
    assert main([str(unchanged_firmware_run(tmp_path))], FakeProvider([FIRMWARE_REPLY])) == 0
    out = capsys.readouterr().out
    assert "fix:   UNKNOWN" in out
    assert "note:  the model chose FIRMWARE" in out


def test_checks_are_off_unless_asked_for(tmp_path: Path) -> None:
    """The pipeline applies no checks by default, so the evaluation can measure the model alone."""
    run = SavedRun.load(unchanged_firmware_run(tmp_path))
    assert triage_run(run, FakeProvider([FIRMWARE_REPLY])).fix is DEFAULT_PACK.action("FIRMWARE")
    checked = triage_run(run, FakeProvider([FIRMWARE_REPLY]), checks=DEFAULT_CHECKS)
    assert checked.fix is UNKNOWN


def test_a_gate_that_objects_answers_unknown_without_asking_the_model(tmp_path: Path) -> None:
    """Disagreeing precedents give UNKNOWN, and the model is never sent a prompt."""
    sources = [Source("past-a", "a", "cable_loss", "CABLE"), Source("past-b", "b", "x", "LINK")]
    provider = FakeProvider([VERDICT])
    verdict = triage_run(saved_run(tmp_path), provider, FULL, sources, gates=DEFAULT_GATES)
    assert verdict.fix is UNKNOWN
    assert verdict.cited == []
    assert "different actions" in verdict.gate_reason
    assert not provider.prompts


@pytest.mark.parametrize("sources", [[], [Source("past-a", "a", "cable_loss", "CABLE", 0.99)]])
def test_gates_that_find_no_objection_leave_the_answer_to_the_model(
    tmp_path: Path, sources: list[Source]
) -> None:
    """No precedents, or close ones that agree: the model is asked as before."""
    provider = FakeProvider([VERDICT])
    verdict = triage_run(saved_run(tmp_path), provider, FULL, sources, gates=DEFAULT_GATES)
    assert verdict.fix is DEFAULT_PACK.action("CABLE")
    assert verdict.gate_reason == ""
    assert len(provider.prompts) == 1


def test_the_model_is_asked_to_cite_only_the_sources_it_was_given(tmp_path: Path) -> None:
    """With sources, the reply's schema lists their ids; without, the provider's default holds."""
    sources = [Source("past-a", "a"), Source("past-b", "b")]
    provider = FakeProvider([VERDICT])
    triage_run(saved_run(tmp_path), provider, FULL, sources)
    triage_run(saved_run(tmp_path), provider, FULL)
    narrowed, default = provider.schemas
    assert narrowed is not None
    properties = narrowed["properties"]
    assert isinstance(properties, dict)
    assert properties["cited"]["items"]["enum"] == ["past-a", "past-b"]
    assert default is None


def test_a_cited_past_run_brings_its_fix_to_the_verdict(tmp_path: Path) -> None:
    """What fixed the cited run last time comes back with the verdict, word for word."""
    sources = [Source("past-1", "loss 1.9 dB", fix_applied="Replaced the cable and recalibrated")]
    reply = json.dumps({"fix": "CABLE", "where": "cable", "because": "same", "cited": ["past-1"]})
    verdict = triage_run(saved_run(tmp_path), FakeProvider([reply]), FULL, sources)
    assert [past.fix_applied for past in verdict.past_fixes] == [
        "Replaced the cable and recalibrated"
    ]


def test_the_cli_states_the_category_and_what_fixed_it_last_time() -> None:
    """The printed verdict says firmware, bench or test, and each cited run's recorded fix."""
    verdict = Verdict(
        DEFAULT_PACK.action("CABLE"),
        "cable",
        "loss",
        cited=["past-1"],
        past_fixes=(PastFix("past-1", "Replaced the cable"),),
    )
    out = describe(verdict)
    assert "cause: bench" in out
    assert "last time: Replaced the cable (past-1)" in out


def test_the_cli_names_one_fix_once_however_many_runs_recorded_it() -> None:
    """Two cited runs fixed the same way print as one line naming both."""
    fix = "Corrected level_offset_db to 1.5"
    verdict = Verdict(
        DEFAULT_PACK.action("CONFIGURATION"),
        "config",
        "x",
        past_fixes=(PastFix("past-1", fix), PastFix("past-2", fix)),
    )
    out = describe(verdict)
    assert out.count("last time:") == 1
    assert f"last time: {fix} (past-1, past-2)" in out
