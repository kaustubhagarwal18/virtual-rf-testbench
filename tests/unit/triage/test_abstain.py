"""Tests for the gates that answer UNKNOWN from the precedents alone."""

import itertools

import pytest

from triage.abstain import (
    DEFAULT_GATES,
    MIN_SIMILARITY,
    PrecedentsDisagree,
    WeakPrecedent,
    reason_to_abstain,
)
from triage.prompt import Fix, Source


def precedent(action: str = "", similarity: float | None = None) -> Source:
    """Return a past run resolved by an action, at a similarity to the question."""
    return Source(f"past-{action}-{similarity}", "text", "fault", action, similarity)


ACTIONS = ["", "CABLE", "LINK", "FIRMWARE"]
"""What a precedent's label may record: nothing, or one of a few keys."""


@pytest.mark.parametrize("actions", list(itertools.product(ACTIONS, repeat=3)))
def test_only_two_different_recorded_actions_count_as_disagreement(
    actions: tuple[str, ...],
) -> None:
    """Every three-precedent mix: abstain exactly when two recorded actions differ."""
    reason = PrecedentsDisagree().reason_to_abstain([precedent(action) for action in actions])
    recorded = {action for action in actions if action}
    assert bool(reason) == (len(recorded) > 1)
    for action in recorded if reason else ():
        assert action in reason


@pytest.mark.parametrize(
    ("similarities", "abstains"),
    [
        ([None, None], False),
        ([0.5], True),
        ([MIN_SIMILARITY], False),
        ([MIN_SIMILARITY - 0.001], True),
        ([0.2, MIN_SIMILARITY + 0.01], False),
        ([None, 0.3], True),
        ([1.0, None], False),
        ([-1.0], True),
    ],
)
def test_a_weak_closest_precedent_abstains_and_an_unknown_one_does_not(
    similarities: list[float | None], abstains: bool
) -> None:
    """The closest scored precedent decides; unscored ones are unknown, not low."""
    sources = [precedent("CABLE", value) for value in similarities]
    reason = WeakPrecedent().reason_to_abstain(sources)
    assert bool(reason) is abstains
    if abstains:
        assert f"{MIN_SIMILARITY:.2f}" in reason


def test_the_threshold_can_be_injected() -> None:
    """A different history may need a different threshold, so it is a parameter."""
    sources = [precedent("CABLE", 0.6)]
    assert WeakPrecedent(0.5).reason_to_abstain(sources) == ""
    assert WeakPrecedent(0.7).reason_to_abstain(sources)


def test_no_precedents_means_the_gates_stay_out_of_the_way() -> None:
    """Without retrieval there is nothing to judge, so the model is asked as before."""
    assert reason_to_abstain([], DEFAULT_GATES) == ""


def test_the_first_gate_that_objects_gives_the_reason() -> None:
    """Disagreement is asked before similarity, so its reason is the one reported."""
    sources = [precedent("CABLE", 0.1), precedent("LINK", 0.1)]
    assert "different actions" in reason_to_abstain(sources, DEFAULT_GATES)
    assert "similar" in reason_to_abstain(sources, (WeakPrecedent(),))
    assert reason_to_abstain(sources, ()) == ""


def test_agreeing_close_precedents_pass_every_gate() -> None:
    """Three close precedents resolved the same way leave the answer to the model."""
    sources = [precedent(Fix.LINK.name, 0.99) for _ in range(3)]
    assert reason_to_abstain(sources, DEFAULT_GATES) == ""


def test_the_default_gates_are_disagreement_then_similarity() -> None:
    """The order is fixed, so a report's reasons are reproducible."""
    assert [type(gate) for gate in DEFAULT_GATES] == [PrecedentsDisagree, WeakPrecedent]
