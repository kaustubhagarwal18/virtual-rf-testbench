"""Tests for triaging CI's failed bench run and writing the verdict for the pull request comment."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.tools.test_triage_demo import StubRetriever, UnreachableProvider
from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from tools.ci_triage import (
    HEADING,
    TEXT_LIMIT,
    main,
    render_problem,
    render_verdict,
    triage_failed_run,
)
from triage.answer import PastFix, Verdict
from triage.evidence import SavedRun
from triage.llm import FakeProvider
from triage.prompt import Fix, Source

CABLE_REPLY = json.dumps(
    {
        "fix": "CABLE",
        "where": "the bench cable",
        "because": "the same loss resolved by a new cable before",
        "cited": ["past-1"],
    }
)

AGREEING = (
    Source("past-1", "loss high; fix: new cable", action="CABLE", fix_applied="Replaced the cable"),
    Source("past-2", "loss high; fix: new cable", action="CABLE", fix_applied="Replaced it again"),
)
DISAGREEING = (
    Source("past-1", "loss high; fix: new cable", action="CABLE", fix_applied="Replaced the cable"),
    Source("past-2", "no signal; fix: link", action="LINK", fix_applied="Reseated the link"),
)

HOSTILE_TEXTS = [
    "@octocat please look",
    '<img src="x" onerror="alert(1)">',
    "[click here](https://example.invalid)",
    "![tracker](https://example.invalid/pixel.png)",
    "a `code span` that tries to close early",
    "```\nfenced\n```",
    "first line\n# a heading on the second\n- and a list item",
    "| a | table |",
    "<!-- virtual-rf-testbench:test-results -->",
    "x" * (TEXT_LIMIT * 3),
]
"""Text a pull request's code could put in the logs, and so steer the model into writing."""


def outside_code_spans(markdown: str) -> str:
    """Return the Markdown with every inline code span removed, leaving what GitHub interprets."""
    return re.sub(r"`[^`\n]*`", "", markdown)


def answered(because: str = "the loss is high", where: str = "the cable") -> Verdict:
    """Return a CABLE verdict citing one past run, with its recorded fix."""
    return Verdict(
        Fix.CABLE,
        where,
        because,
        ["past-1"],
        past_fixes=(PastFix("past-1", "Replaced the cable and recalibrated the path loss"),),
    )


def test_an_answer_shows_action_cause_reason_citations_and_last_fix() -> None:
    """The section names the action and its cause, the reason, and quotes what fixed it."""
    section = render_verdict(answered())
    assert section.startswith(HEADING)
    assert "`CABLE`" in section
    assert "**bench**" in section
    assert "`the loss is high`" in section
    assert "`past-1`" in section
    assert "`Replaced the cable and recalibrated the path loss`" in section


def test_a_gated_unknown_says_why_the_model_was_not_asked() -> None:
    """When the past runs disagree, the section says UNKNOWN and gives the gate's reason."""
    section = render_verdict(Verdict.gated("the closest past runs were resolved differently"))
    assert "`UNKNOWN`" in section
    assert "`the closest past runs were resolved differently`" in section


def test_an_overruled_answer_gives_the_check_instead_of_the_model_words() -> None:
    """A verdict a check replaced says why, and hides the refused answer's reasoning."""
    verdict = Verdict(
        Fix.UNKNOWN,
        "LINK",
        "the model's refused reasoning",
        ["past-1"],
        overruled="no signal was logged",
        past_fixes=(PastFix("past-1", "Reconnected the cable"),),
    )
    section = render_verdict(verdict)
    assert "`no signal was logged`" in section
    assert "refused reasoning" not in section
    assert "**Why:**" not in section
    assert "`Reconnected the cable`" in section


def test_a_model_unknown_keeps_its_own_reason() -> None:
    """When the model itself answers UNKNOWN, its reason is the answer and is shown."""
    section = render_verdict(Verdict(Fix.UNKNOWN, "", "nothing in the run points anywhere"))
    assert "`nothing in the run points anywhere`" in section
    assert "- **Where:** (none)" in section


def test_past_runs_with_the_same_fix_share_one_line() -> None:
    """Three cited runs fixed the same way are listed once, with all three ids."""
    fixes = tuple(PastFix(f"past-{number}", "Replaced the cable") for number in (1, 2, 3))
    section = render_verdict(Verdict(Fix.CABLE, "x", "y", ["past-1"], past_fixes=fixes))
    last_time = [line for line in section.splitlines() if line.startswith("- **Last time:**")]
    assert last_time == ["- **Last time:** `Replaced the cable` (`past-1`, `past-2`, `past-3`)"]


def verdict_with(field: str, text: str) -> Verdict:
    """Return a verdict that carries the text in the named field and plain text elsewhere."""
    if field == "gate_reason":
        return Verdict.gated(text)
    if field == "past_fix":
        return Verdict(Fix.CABLE, "x", "y", ["p"], past_fixes=(PastFix("p", text),))
    return answered(**{field: text})


@pytest.mark.parametrize("hostile", HOSTILE_TEXTS)
@pytest.mark.parametrize("field", ["because", "where", "past_fix", "gate_reason"])
def test_untrusted_text_is_only_ever_shown_as_code(hostile: str, field: str) -> None:
    """Whatever the logs make the model write stays inside one code span on one line."""
    section = render_verdict(verdict_with(field, hostile))
    baseline = render_verdict(verdict_with(field, "z"))
    assert outside_code_spans(section) == outside_code_spans(baseline)
    assert len(section.splitlines()) == len(baseline.splitlines())
    assert all(len(line) < TEXT_LIMIT + 200 for line in section.splitlines())


def test_a_problem_is_one_line_under_the_heading() -> None:
    """When there is no verdict, the section says why in one line, as code."""
    section = render_problem("model unreachable <b>now</b>")
    assert section.startswith(HEADING)
    assert "No verdict" in section
    assert "<b>" not in outside_code_spans(section)


def test_the_failed_run_is_triaged_as_the_api_triages_it(tmp_path: Path) -> None:
    """Agreeing past runs reach the model; both share its action, so both are quoted."""
    run = SavedRun.load(write_run(tmp_path / "run", [FAILING_TEST]))
    provider = FakeProvider([CABLE_REPLY])
    verdict = triage_failed_run(run, provider, StubRetriever(*AGREEING))
    assert verdict.fix is Fix.CABLE
    assert verdict.past_fixes == (
        PastFix("past-1", "Replaced the cable"),
        PastFix("past-2", "Replaced it again"),
    )
    assert len(provider.prompts) == 1


def test_disagreeing_past_runs_answer_unknown_without_asking_the_model(tmp_path: Path) -> None:
    """The API's disagreement gate applies in CI too: no model call, an UNKNOWN with a reason."""
    run = SavedRun.load(write_run(tmp_path / "run", [FAILING_TEST]))
    provider = FakeProvider([])
    verdict = triage_failed_run(run, provider, StubRetriever(*DISAGREEING))
    assert verdict.fix is Fix.UNKNOWN
    assert verdict.gate_reason
    assert not provider.prompts


def test_main_writes_the_verdict_section(tmp_path: Path) -> None:
    """A triaged run writes its section to the output file and exits 0."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    out = tmp_path / "triage.md"
    code = main(
        [str(folder), "--out", str(out)], FakeProvider([CABLE_REPLY]), StubRetriever(*AGREEING)
    )
    assert code == 0
    assert "`CABLE`" in out.read_text(encoding="utf-8")


def test_main_writes_a_problem_when_the_run_is_missing(tmp_path: Path) -> None:
    """No saved run means the bench failed before the suite wrote results; the section says so."""
    out = tmp_path / "triage.md"
    code = main(
        [str(tmp_path / "absent"), "--out", str(out)], FakeProvider([]), StubRetriever(*AGREEING)
    )
    assert code == 1
    assert "No verdict" in out.read_text(encoding="utf-8")


def test_main_writes_a_problem_when_the_model_is_unreachable(tmp_path: Path) -> None:
    """An unreachable model still leaves a section, so the comment explains the gap."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    out = tmp_path / "triage.md"
    code = main([str(folder), "--out", str(out)], UnreachableProvider(), StubRetriever(*AGREEING))
    assert code == 1
    assert "ollama serve" in out.read_text(encoding="utf-8")


def test_runs_as_a_module(tmp_path: Path) -> None:
    """Python -m tools.ci_triage works as CI calls it, writing a section for a missing run."""
    out = tmp_path / "triage.md"
    completed = subprocess.run(
        [sys.executable, "-m", "tools.ci_triage", str(tmp_path / "absent"), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "No verdict" in out.read_text(encoding="utf-8")


def test_an_answer_says_what_to_do_in_the_action_list_words() -> None:
    """The action's own description, fixed text from the action list, says what to do."""
    assert f"- **What to do:** {Fix.CABLE.value}" in render_verdict(answered())


@pytest.mark.parametrize(
    "verdict",
    [Verdict.gated("disagree"), Verdict(Fix.UNKNOWN, "", "nothing points anywhere")],
)
def test_an_unknown_has_nothing_to_do(verdict: Verdict) -> None:
    """UNKNOWN is no action, so there is no 'What to do' line."""
    assert "What to do" not in render_verdict(verdict)
