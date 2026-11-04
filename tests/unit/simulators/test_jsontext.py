"""Tests for standard JSON text: NaN and infinities become strings."""

import math

import pytest

from simulators.common.jsontext import json_safe, to_json
from tests.support.strict_json import strict_loads


def test_non_finite_numbers_become_strings_at_any_depth() -> None:
    """NaN and both infinities are replaced inside dicts, lists and tuples; other values stay."""
    data = {"level": math.nan, "readings": [1.5, math.inf, (-math.inf, "x")], "ok": True}
    assert json_safe(data) == {
        "level": "NaN",
        "readings": [1.5, "Infinity", ["-Infinity", "x"]],
        "ok": True,
    }


def test_to_json_writes_standard_json() -> None:
    """The text parses with a reader that rejects NaN and Infinity."""
    text = to_json({"margin_db": -math.inf, "value": 1})
    assert strict_loads(text) == {"margin_db": "-Infinity", "value": 1}


def test_unknown_types_fail_unless_stringified() -> None:
    """A value JSON has no type for raises, unless the caller asks for str() instead."""
    with pytest.raises(TypeError):
        to_json({"when": object()})
    assert to_json({"size": 3j}, stringify_unknown=True) == '{"size": "3j"}'
