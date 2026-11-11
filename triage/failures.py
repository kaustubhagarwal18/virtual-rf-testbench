"""Splitting a failed run into its distinct failures, and triaging each one on its own.

A run can fail in more than one way: a firmware release can break one command and change a
frequency elsewhere. Triaging only the first failure answers for one of them. Here the failed
tests are grouped, so that tests failing the same way are asked about once: the tests a
failed setup step stopped are one failure, and so are the cases of one test that fail with
the same kind of error. Each group becomes a focused copy of the run, which the usual
pipeline triages like any other run.

The number of groups triaged is capped: each is one model prompt, which takes up to a
minute on a CPU, and a run with eleven distinct failures shouldn't hold CI for ten. The
groups past the cap are listed, not dropped.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from triage.abstain import SERVICE_GATES
from triage.answer import MalformedAnswerError, Verdict
from triage.case import Case, Focus, Item
from triage.checks import DEFAULT_CHECKS, checks_for
from triage.evidence import FULL
from triage.llm import LLMProvider
from triage.pack import DEFAULT_PACK, Pack
from triage.retrieval import DEFAULT_TOP_K, Retriever
from triage.service import triage_run

DEFAULT_MAX_FAILURES = 5
"""How many distinct failures to triage in one run; the rest are listed as not triaged."""

_STOPPED_BY_STEP = re.compile(
    r"StepFailedError: Step \d+ \((?P<action>[^)]*)\) failed on the (?P<instrument>\S+)"
)
"""What a test stopped by a failed setup step reports, naming the step's action."""


@dataclass(frozen=True)
class FailureGroup:
    """Failed tests that failed the same way, triaged as one.

    Attributes:
        description: What failed, such as 'test_level_accuracy: Failed' or a setup step.
        tests: The full ids of the tests in the group, in the order they ran.
        step: The failed setup step's (action, instrument) when the group is one; None for
            a test's own failure.
    """

    description: str
    tests: tuple[str, ...] = ()
    step: tuple[str, str] | None = None

    @property
    def test_ids(self) -> tuple[str, ...]:
        """The tests' names without their file, as a reader scans them.

        Returns:
            Each test's name and parameters, such as 'test_level_accuracy[-30.0]'.
        """
        return tuple(test_id.split("::")[-1] for test_id in self.tests)


def _failure_kind(test: Item) -> str:
    """Name the kind of failure an item reported: its exception's class, or 'Failed'.

    Args:
        test: The failed item.

    Returns:
        The class name before the message's first colon, without its module, or the
        test's outcome when the message names none.
    """
    head, separator, _ = test.message.partition(":")
    if not separator or " " in head.strip():
        return test.outcome
    return head.strip().rsplit(".", 1)[-1]


def _function(item_id: str) -> str:
    """Name what an item is a case of, so a test's parametrized cases group together.

    A pytest id ('file.py::test_level[-30.0]') names a test function and, in brackets, one
    case of it. Any other id is its own thing: 'payment[1]' and 'payment[2]' are two
    payments, not two cases of one test.

    Args:
        item_id: The item's id.

    Returns:
        The test function for a pytest id, or the whole id otherwise.
    """
    if "::" not in item_id:
        return item_id
    return item_id.rsplit("::", maxsplit=1)[-1].split("[", maxsplit=1)[0]


def failure_groups(run: Case) -> list[FailureGroup]:
    """Group a run's failed tests by how they failed.

    Failed setup steps come first, as first_failure puts them first: every test such a step
    stopped joins its group. Each remaining item joins the group of its test function (for a
    pytest id; any other item is its own) and kind of failure, in the order they ran.

    Args:
        run: The run.

    Returns:
        The distinct failures, empty when every test passed.
    """
    steps: dict[tuple[str, str], list[str]] = {}
    for step in run.failed_steps:
        steps.setdefault((step.action, step.target), [])
    tests: dict[tuple[str, str], list[str]] = {}
    for test in run.failed_items:
        test_id = test.id
        stopped = _STOPPED_BY_STEP.search(test.message)
        if stopped:
            steps.setdefault((stopped["action"], stopped["instrument"]), []).append(test_id)
            continue
        function = _function(test_id)
        tests.setdefault((function, _failure_kind(test)), []).append(test_id)
    return [
        FailureGroup(f"setup step '{action}' on the {instrument}", tuple(ids), (action, instrument))
        for (action, instrument), ids in steps.items()
    ] + [FailureGroup(f"{function}: {kind}", tuple(ids)) for (function, kind), ids in tests.items()]


def focus(run: Case, group: FailureGroup, groups: Sequence[FailureGroup]) -> Case:
    """Make a copy of the run in which only this group's tests failed.

    The passed tests, the logs, the versions and the run's totals stay, since they describe
    the whole run; the other groups' failed tests and failed steps go, and their
    descriptions are recorded so the evidence can say they exist.

    Args:
        run: The run.
        group: The failure to focus on.
        groups: Every failure in the run, this one included.

    Returns:
        The focused run, which the usual pipeline triages like any other.
    """
    kept = set(group.tests)
    return replace(
        run,
        items=tuple(item for item in run.items if not item.failed or item.id in kept),
        steps=tuple(
            step
            for step in run.steps
            if not step.failed or group.step == (step.action, step.target)
        ),
        focus=Focus(len(group.tests), tuple(o.description for o in groups if o != group)),
    )


@dataclass(frozen=True)
class FailureAnswer:
    """The answer for one distinct failure.

    Attributes:
        group: The failure.
        verdict: What triage decided; None when the model's reply couldn't be used.
        problem: Why there is no verdict, when there isn't one.
    """

    group: FailureGroup
    verdict: Verdict | None
    problem: str = ""


@dataclass(frozen=True)
class RunTriage:
    """Every answer for one run.

    Attributes:
        answers: One per failure triaged, in the order the failures were found.
        untriaged: The failures past the cap, not asked about.
        failed_tests: How many tests failed or errored in the run.
        distinct_failures: How many groups those tests formed.
        limit: How many of them were to be triaged at most.
    """

    answers: list[FailureAnswer] = field(default_factory=list)
    untriaged: list[FailureGroup] = field(default_factory=list)
    failed_tests: int = 0
    distinct_failures: int = 0
    limit: int = DEFAULT_MAX_FAILURES


def triage_failures(
    run: Case,
    answer: Callable[[Case], Verdict],
    limit: int = DEFAULT_MAX_FAILURES,
) -> RunTriage:
    """Triage each distinct failure in a run, up to the cap.

    Args:
        run: The failed run.
        answer: How one focused run is triaged, such as triage_like_the_api.
        limit: How many failures to ask about; the rest are listed as untriaged.

    Returns:
        One answer per failure triaged, even where two agree: each failure is something
        to act on. A reply that couldn't be used becomes that failure's problem; an
        unreachable model still stops the whole run.
    """
    groups = failure_groups(run)
    answers = []
    for group in groups[:limit]:
        try:
            answers.append(FailureAnswer(group, answer(focus(run, group, groups))))
        except MalformedAnswerError as unusable:
            answers.append(FailureAnswer(group, None, f"the model's answer: {unusable}"))
    return RunTriage(
        answers=answers,
        untriaged=groups[limit:],
        failed_tests=len(run.failed_items),
        distinct_failures=len(groups),
        limit=limit,
    )


def triage_like_the_api(
    run: Case, provider: LLMProvider, retriever: Retriever, pack: Pack = DEFAULT_PACK
) -> Verdict:
    """Triage a run as the API does: past runs, the disagreement gate, the model, the checks.

    Args:
        run: The run, whole or focused on one failure.
        provider: The answering model.
        retriever: The source of past runs.
        pack: The domain pack, which supplies the actions and the checks.

    Returns:
        The verdict, UNKNOWN when the gate or a check found no safe answer.
    """
    sources = retriever.similar_to(run, DEFAULT_TOP_K)
    checks = DEFAULT_CHECKS if pack == DEFAULT_PACK else checks_for(pack)
    return triage_run(run, provider, FULL, sources, checks=checks, gates=SERVICE_GATES, pack=pack)
