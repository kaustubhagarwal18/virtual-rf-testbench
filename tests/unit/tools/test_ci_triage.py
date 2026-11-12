"""Tests for triaging CI's failed bench run and writing the verdict for the pull request comment."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tests.unit.tools.test_triage_demo import StubRetriever, UnreachableProvider
from tests.unit.triage.test_evidence import FAILING_TEST, write_run
from tests.unit.triage.test_pack import VALID, payments_case, write_pack
from tools.ci_triage import (
    HEADING,
    NO_SAFE_ANSWER,
    SHOWN_TESTS,
    TEXT_LIMIT,
    history_for,
    main,
    positive_count,
    render_problem,
    render_triage,
)
from triage.answer import PastFix, Verdict
from triage.bench_run import load_case
from triage.embed import FakeEmbedder
from triage.failures import FailureAnswer, FailureGroup, RunTriage, triage_like_the_api
from triage.llm import FakeProvider
from triage.pack import DEFAULT_PACK, UNKNOWN, load_pack
from triage.prompt import Source
from triage.retrieval import DEFAULT_HISTORY, InMemoryRetriever

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


LEVEL_GROUP = FailureGroup(
    "test_level_at_1ghz: Failed", ("tests/bench/test_bench.py::test_level_at_1ghz",)
)


def render_verdict(verdict: Verdict, group: FailureGroup = LEVEL_GROUP) -> str:
    """Render the section for a run with one failure that got this verdict."""
    return render_triage(
        RunTriage([FailureAnswer(group, verdict)], failed_tests=1, distinct_failures=1)
    )


def failing_test(name: str) -> dict[str, object]:
    """Return FAILING_TEST under another test name, so it is a failure of its own."""
    return {**FAILING_TEST, "id": f"tests/bench/test_bench.py::{name}"}


def outside_code_spans(markdown: str) -> str:
    """Return the Markdown with every inline code span removed, leaving what GitHub interprets."""
    return re.sub(r"`[^`\n]*`", "", markdown)


def answered(because: str = "the loss is high", where: str = "the cable") -> Verdict:
    """Return a CABLE verdict citing one past run, with its recorded fix."""
    return Verdict(
        DEFAULT_PACK.action("CABLE"),
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
    """A verdict a check replaced says why, and hides the refused answer and its past fix."""
    verdict = Verdict(
        UNKNOWN,
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
    assert "Last time" not in section


def test_a_model_unknown_keeps_its_own_reason() -> None:
    """When the model itself answers UNKNOWN, its reason is the answer and is shown."""
    section = render_verdict(Verdict(UNKNOWN, "", "nothing in the run points anywhere"))
    assert "`nothing in the run points anywhere`" in section
    assert "- **Where:** (none)" in section


def test_past_runs_with_the_same_fix_share_one_line() -> None:
    """Three cited runs fixed the same way are listed once, with all three ids."""
    fixes = tuple(PastFix(f"past-{number}", "Replaced the cable") for number in (1, 2, 3))
    section = render_verdict(
        Verdict(DEFAULT_PACK.action("CABLE"), "x", "y", ["past-1"], past_fixes=fixes)
    )
    last_time = [line for line in section.splitlines() if line.startswith("- **Last time:**")]
    assert last_time == ["- **Last time:** `Replaced the cable` (`past-1`, `past-2`, `past-3`)"]


def verdict_with(field: str, text: str) -> Verdict:
    """Return a verdict that carries the text in the named field and plain text elsewhere."""
    if field == "gate_reason":
        return Verdict.gated(text)
    if field == "past_fix":
        return Verdict(
            DEFAULT_PACK.action("CABLE"), "x", "y", ["p"], past_fixes=(PastFix("p", text),)
        )
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
    run = load_case(write_run(tmp_path / "run", [FAILING_TEST]))
    provider = FakeProvider([CABLE_REPLY])
    verdict = triage_like_the_api(run, provider, StubRetriever(*AGREEING))
    assert verdict.fix is DEFAULT_PACK.action("CABLE")
    assert verdict.past_fixes == (
        PastFix("past-1", "Replaced the cable"),
        PastFix("past-2", "Replaced it again"),
    )
    assert len(provider.prompts) == 1


def test_disagreeing_past_runs_answer_unknown_without_asking_the_model(tmp_path: Path) -> None:
    """The API's disagreement gate applies in CI too: no model call, an UNKNOWN with a reason."""
    run = load_case(write_run(tmp_path / "run", [FAILING_TEST]))
    provider = FakeProvider([])
    verdict = triage_like_the_api(run, provider, StubRetriever(*DISAGREEING))
    assert verdict.fix is UNKNOWN
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
    assert f"- **What to do:** {DEFAULT_PACK.action('CABLE').says}" in render_verdict(answered())


@pytest.mark.parametrize(
    "verdict",
    [Verdict.gated("disagree"), Verdict(UNKNOWN, "", "nothing points anywhere")],
)
def test_an_unknown_says_to_look_by_hand(verdict: Verdict) -> None:
    """UNKNOWN has no action, so what to do is to look at the failure by hand."""
    assert f"- **What to do:** {NO_SAFE_ANSWER}" in render_verdict(verdict)


def test_a_single_failure_names_its_tests_and_says_it_is_one() -> None:
    """With one failure, the summary says so and the answer lists the failed test."""
    section = render_verdict(answered())
    assert "1 failed test: 1 distinct failure." in section
    assert "- **Tests (1):** `test_level_at_1ghz`" in section


def test_each_answer_lists_the_tests_it_covers_and_counts_the_rest() -> None:
    """Many tests are named up to a few, then counted, so the block stays short."""
    tests = tuple(f"tests/x.py::test_case[{number}]" for number in range(SHOWN_TESTS + 2))
    section = render_verdict(answered(), FailureGroup("test_case: Failed", tests))
    assert f"- **Tests ({SHOWN_TESTS + 2}):** `test_case[0]`" in section
    assert "and 2 more" in section
    assert "`test_case[3]`" not in section


def test_every_failure_gets_a_numbered_block_saying_what_to_do() -> None:
    """Each failure is named in its own heading and says what to do, even if answers agree."""
    other = FailureGroup("test_other: Failed", ("tests/x.py::test_other",))
    third = FailureGroup("test_third: Failed", ("tests/x.py::test_third",))
    result = RunTriage(
        [
            FailureAnswer(LEVEL_GROUP, answered()),
            FailureAnswer(
                other, Verdict(DEFAULT_PACK.action("ALIGNMENT"), "analyzer", "reads high", [])
            ),
            FailureAnswer(third, answered()),
        ],
        failed_tests=4,
        distinct_failures=3,
    )
    section = render_triage(result)
    assert "4 failed tests: 3 distinct failures, each triaged on its own below." in section
    assert section.count("- **What to do:**") == len(result.answers)
    assert "#### Failure 1 of 3: `test_level_at_1ghz: Failed`" in section
    assert "#### Failure 2 of 3: `test_other: Failed`" in section
    assert "#### Failure 3 of 3: `test_third: Failed`" in section
    cable = [
        answer
        for answer in result.answers
        if answer.verdict and answer.verdict.fix is DEFAULT_PACK.action("CABLE")
    ]
    assert section.count("`CABLE`") == len(cable)


def test_failures_past_the_cap_are_named_as_not_triaged() -> None:
    """A failure left out by the cap is listed with its test count, as code."""
    skipped = FailureGroup("<b>test_x</b>: Failed", ("a::test_x", "a::test_y"))
    result = RunTriage(
        [FailureAnswer(LEVEL_GROUP, answered())],
        untriaged=[skipped],
        failed_tests=3,
        distinct_failures=2,
        limit=1,
    )
    section = render_triage(result)
    assert "**Not triaged** (at most 1 failure per run): `<b>test_x</b>: Failed` (2 tests)" in (
        section
    )
    assert "<b>" not in outside_code_spans(section)


def test_a_failure_without_a_usable_reply_says_why_and_keeps_the_others() -> None:
    """An unusable reply for one failure is that block's problem; the other block stands."""
    result = RunTriage(
        [
            FailureAnswer(LEVEL_GROUP, None, "the model's answer: not JSON"),
            FailureAnswer(FailureGroup("t: Failed", ("a::t",)), answered()),
        ],
        failed_tests=2,
        distinct_failures=2,
    )
    section = render_triage(result)
    assert "- **Answer:** none" in section
    assert f"- **What to do:** {NO_SAFE_ANSWER}" in section
    assert "- **Problem:** `the model's answer: not JSON`" in section
    assert "`CABLE`" in section


@pytest.mark.parametrize("hostile", HOSTILE_TEXTS)
def test_test_names_are_only_ever_shown_as_code(hostile: str) -> None:
    """A test name the pull request chose can't add markup to the comment."""
    group = FailureGroup(hostile, (f"tests/x.py::{hostile}",))
    section = render_verdict(answered(), group)
    baseline = render_verdict(answered(), FailureGroup("z", ("tests/x.py::z",)))
    assert outside_code_spans(section) == outside_code_spans(baseline)


def test_main_asks_once_per_failure_and_shows_each(tmp_path: Path) -> None:
    """Two failures that both get CABLE cost two prompts and show two blocks."""
    tests = [failing_test("test_a"), failing_test("test_b")]
    folder = write_run(tmp_path / "run", tests)
    out = tmp_path / "triage.md"
    provider = FakeProvider([CABLE_REPLY])
    code = main([str(folder), "--out", str(out)], provider, StubRetriever(*AGREEING))
    section = out.read_text(encoding="utf-8")
    assert code == 0
    assert len(provider.prompts) == len(tests)
    assert section.count("`CABLE`") == len(tests)
    assert "#### Failure 2 of 2: `test_b: Failed`" in section


def test_main_triages_only_as_many_failures_as_asked(tmp_path: Path) -> None:
    """--max-failures 1 asks about the first failure and lists the second."""
    folder = write_run(tmp_path / "run", [failing_test("test_a"), failing_test("test_b")])
    out = tmp_path / "triage.md"
    provider = FakeProvider([CABLE_REPLY])
    arguments = [str(folder), "--out", str(out), "--max-failures", "1"]
    assert main(arguments, provider, StubRetriever(*AGREEING)) == 0
    assert len(provider.prompts) == 1
    assert "**Not triaged**" in out.read_text(encoding="utf-8")


def test_main_writes_a_problem_when_no_test_failed(tmp_path: Path) -> None:
    """A run with nothing failed has nothing to triage; the section says so."""
    folder = write_run(tmp_path / "run", [])
    out = tmp_path / "triage.md"
    assert main([str(folder), "--out", str(out)], FakeProvider([]), StubRetriever()) == 1
    assert "no failed test" in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("text", ["0", "-1", "two", ""])
def test_the_cap_must_be_a_whole_number_of_at_least_one(text: str) -> None:
    """A cap of nothing, or not a number, is refused with a message."""
    with pytest.raises(argparse.ArgumentTypeError, match="at least 1"):
        positive_count(text)


def test_main_triages_with_another_domains_pack(tmp_path: Path) -> None:
    """--pack swaps the RF actions for the pack's: the answer names a payments action."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    pack = write_pack(tmp_path, VALID)
    out = tmp_path / "triage.md"
    payment_reply = json.dumps(
        {"fix": "REQUEST_FEED", "where": "the feed", "because": "short", "cited": []}
    )
    arguments = [str(folder), "--out", str(out), "--pack", str(pack)]
    assert main(arguments, FakeProvider([payment_reply]), StubRetriever()) == 0
    section = out.read_text(encoding="utf-8")
    assert "`REQUEST_FEED`" in section
    assert "ask the provider to resend the file" in section


def test_main_writes_a_problem_when_the_pack_is_broken(tmp_path: Path) -> None:
    """A pack that can't be used leaves a one-line problem in the comment, not a traceback."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    out = tmp_path / "triage.md"
    arguments = [str(folder), "--out", str(out), "--pack", str(tmp_path / "absent.toml")]
    assert main(arguments, FakeProvider([]), StubRetriever()) == 1
    assert "no pack file" in out.read_text(encoding="utf-8")


def test_another_pack_needs_its_own_history(tmp_path: Path) -> None:
    """Without --history, a non-RF pack would be offered the RF past runs; it is refused."""
    folder = write_run(tmp_path / "run", [FAILING_TEST])
    out = tmp_path / "triage.md"
    arguments = [str(folder), "--out", str(out), "--pack", str(write_pack(tmp_path, VALID))]
    assert main(arguments, FakeProvider([])) == 1
    assert "--history" in out.read_text(encoding="utf-8")


def test_a_generic_case_is_triaged_and_grouped_by_its_own_ids(tmp_path: Path) -> None:
    """A payments batch saved as case.json goes through the CI tool with the payments pack."""
    folder = payments_case(tmp_path).folder
    out = tmp_path / "triage.md"
    payment_reply = json.dumps(
        {"fix": "REQUEST_FEED", "where": "the feed", "because": "short", "cited": []}
    )
    arguments = [str(folder), "--out", str(out), "--pack", str(write_pack(tmp_path, VALID))]
    assert main(arguments, FakeProvider([payment_reply]), StubRetriever()) == 0
    section = out.read_text(encoding="utf-8")
    assert "`batch-7/settlement: failed`" in section
    assert "`REQUEST_FEED`" in section


def test_a_broken_case_json_is_a_problem_line(tmp_path: Path) -> None:
    """A case.json that can't be read leaves a one-line problem, not a traceback."""
    (tmp_path / "case.json").write_text('{"format": "triage-case/9"}', encoding="utf-8")
    out = tmp_path / "triage.md"
    assert main([str(tmp_path), "--out", str(out)], FakeProvider([]), StubRetriever()) == 1
    assert "triage-case/9" in out.read_text(encoding="utf-8")


def test_the_history_is_the_one_given_or_the_rf_benchs_for_the_rf_pack(tmp_path: Path) -> None:
    """--history wins; without it only the RF pack falls back to the RF history."""
    assert history_for(tmp_path, load_pack(write_pack(tmp_path, VALID))) == tmp_path
    assert history_for(None, DEFAULT_PACK) == DEFAULT_HISTORY


def test_an_empty_retriever_given_is_used_not_replaced(tmp_path: Path) -> None:
    """An empty index is still the one asked for: no history is loaded, nothing is embedded."""
    folder = payments_case(tmp_path).folder
    out = tmp_path / "triage.md"
    embedder = FakeEmbedder()
    empty = InMemoryRetriever([], np.zeros((0, 0)), embedder)
    payment_reply = json.dumps(
        {"fix": "REQUEST_FEED", "where": "the feed", "because": "short", "cited": []}
    )
    arguments = [str(folder), "--out", str(out), "--pack", str(write_pack(tmp_path, VALID))]
    assert main(arguments, FakeProvider([payment_reply]), empty) == 0
    assert not embedder.calls


def test_invalid_json_in_the_case_is_a_problem_line(tmp_path: Path) -> None:
    """A case.json that isn't JSON leaves a problem section naming it, not a traceback."""
    (tmp_path / "case.json").write_text("{", encoding="utf-8")
    out = tmp_path / "triage.md"
    assert main([str(tmp_path), "--out", str(out)], FakeProvider([]), StubRetriever()) == 1
    assert "case.json" in out.read_text(encoding="utf-8")
