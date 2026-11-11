"""Tests for domain packs: the file that says what a domain's failures are and what to do."""

from pathlib import Path

import pytest

from triage.pack import (
    DEFAULT_PACK,
    UNKNOWN,
    Pack,
    PackError,
    load_pack,
    system_prompt,
)

RF_KEYS = ["CABLE", "LINK", "ALIGNMENT", "CONFIGURATION", "TIMEOUT", "TEST_CLEANUP", "FIRMWARE"]
"""The RF bench pack's actions, in the order its file lists them."""

VALID = '''
name = "payments"
description = """You triage failed payment batches."""
categories = ["feed", "mapping"]

[[actions]]
key = "REQUEST_FEED"
category = "feed"
says = "ask the provider to resend the file"

[[actions]]
key = "FIX_MAPPING"
category = "mapping"
says = "correct the field mapping"
'''
"""A small pack from another domain, to show nothing about RF is assumed."""


def write_pack(tmp_path: Path, text: str) -> Path:
    """Write a pack file and return its path."""
    path = tmp_path / "pack.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_default_pack_is_the_rf_bench_with_unknown_last() -> None:
    """The repository's own pack lists its seven actions, then UNKNOWN, which every pack has."""
    assert DEFAULT_PACK.name == "rf-bench"
    assert list(DEFAULT_PACK.keys) == [*RF_KEYS, "UNKNOWN"]
    assert DEFAULT_PACK.actions[-1] is UNKNOWN
    assert UNKNOWN.category == "unknown"


def test_a_pack_from_another_domain_loads_with_its_own_actions(tmp_path: Path) -> None:
    """A payments pack has payments actions and categories; nothing RF is added."""
    pack = load_pack(write_pack(tmp_path, VALID))
    assert pack.keys == ("REQUEST_FEED", "FIX_MAPPING", "UNKNOWN")
    assert pack.action("FIX_MAPPING").category == "mapping"
    assert pack.categories == ("feed", "mapping")


def test_an_action_is_found_by_its_key_and_an_unknown_key_says_which_exist() -> None:
    """Looking up a key that isn't in the pack names the keys that are."""
    assert DEFAULT_PACK.action("CABLE").category == "bench"
    with pytest.raises(KeyError, match="CABLE"):
        DEFAULT_PACK.action("REBOOT")


BROKEN = {
    "duplicate key": VALID.replace('key = "FIX_MAPPING"', 'key = "REQUEST_FEED"'),
    "UNKNOWN redefined": VALID.replace('key = "FIX_MAPPING"', 'key = "UNKNOWN"'),
    "category not listed": VALID.replace('category = "mapping"', 'category = "network"'),
    "missing says": VALID.replace('says = "correct the field mapping"', ""),
    "no actions": VALID.split("[[actions]]", maxsplit=1)[0],
    "no name": VALID.replace('name = "payments"', ""),
    "lower-case key": VALID.replace('key = "FIX_MAPPING"', 'key = "fix_mapping"'),
    "not TOML": "name = ",
    "categories not a list": VALID.replace('["feed", "mapping"]', '"feed"'),
}
"""Pack files with one mistake each."""


@pytest.mark.parametrize("problem", sorted(BROKEN))
def test_a_broken_pack_is_refused_with_one_line_naming_the_file(
    tmp_path: Path, problem: str
) -> None:
    """Each mistake is refused before anything uses the pack, with the file in the message."""
    path = write_pack(tmp_path, BROKEN[problem])
    with pytest.raises(PackError) as refused:
        load_pack(path)
    assert str(path) in str(refused.value)
    assert "\n" not in str(refused.value)


def test_a_missing_pack_file_is_refused(tmp_path: Path) -> None:
    """A path with no file says so, rather than raising a bare FileNotFoundError."""
    with pytest.raises(PackError, match="no pack file"):
        load_pack(tmp_path / "absent.toml")


def test_the_system_prompt_describes_the_domain_and_lists_every_action(tmp_path: Path) -> None:
    """The prompt is built from the pack: its description, then each action and what it says."""
    pack = load_pack(write_pack(tmp_path, VALID))
    prompt = system_prompt(pack)
    assert prompt.startswith("You triage failed payment batches.")
    for action in pack.actions:
        assert f"- {action.key}: {action.says}" in prompt
    assert "RF" not in prompt


def test_a_pack_is_a_plain_value() -> None:
    """Packs compare by content, so two loads of one file are equal."""
    assert isinstance(DEFAULT_PACK, Pack)
    assert load_pack(Path("triage/packs/rf-bench.toml")) == DEFAULT_PACK
