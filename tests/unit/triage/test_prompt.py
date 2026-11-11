"""Tests for the fix list's categories: firmware, bench, test, or unknown."""

import pytest

from tools.faults import CATALOGUE
from triage.pack import DEFAULT_PACK, UNKNOWN, Action


@pytest.mark.parametrize("fix", list(DEFAULT_PACK.actions))
def test_every_fix_belongs_to_exactly_one_category(fix: Action) -> None:
    """Each action on the list says which of the three causes it answers, or unknown."""
    assert fix.category in {"firmware", "bench", "test", "unknown"}


def test_only_unknown_has_no_cause() -> None:
    """UNKNOWN is the one action that names no cause."""
    assert [fix for fix in DEFAULT_PACK.actions if fix.category == "unknown"] == [UNKNOWN]


@pytest.mark.parametrize("name", sorted(CATALOGUE))
def test_each_faults_category_is_the_category_of_its_action(name: str) -> None:
    """The label's category and the category of its action agree, so the answer states it right."""
    label = CATALOGUE[name].label
    assert DEFAULT_PACK.action(label.action).category == label.category
