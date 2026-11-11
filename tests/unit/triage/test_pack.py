"""Tests for domain packs: the file that says what a domain's failures are and what to do."""

from pathlib import Path

import pytest

from triage.bench_run import load_case
from triage.case import Case, Context, Item, LogLine, Reading, save_case
from triage.facts import ReadingFailedIn, ReadingsFailedOnlyOutside
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


WITH_CHECKS = (
    VALID
    + """
[facts.feed_error]
kind = "errors_reported"

[facts.mapping_changed_message]
kind = "log_contains"
text = "mapping v2 deployed"

[[checks]]
name = "mapping-needs-a-change"
refuse = "FIX_MAPPING"
unless = "mapping_changed_message"
reason = "no mapping change was deployed"

[[checks]]
name = "feed-error-is-not-mapping"
refuse = "FIX_MAPPING"
when = "feed_error"
reason = "the feed reported {error}"
"""
)
"""The payments pack with two facts and two checks."""


def payments_case(tmp_path: Path, logs: tuple[str, ...] = ("batch 7 rejected",)) -> Case:
    """Save a failed payments batch as a generic case.json, with no bench format, and load it."""
    batch = Item(
        id="batch-7/settlement",
        outcome="failed",
        message="3 of 120 payments rejected",
        errors=("E17 file truncated",),
        readings=(Reading("rejected", 3, "payments", 0, 0, -3, passed=False),),
    )
    case = Case(
        folder=tmp_path / "batch",
        items=(batch, Item("batch-7/fx", "passed")),
        logs=tuple(LogLine(line) for line in logs),
        context=Context(versions={"mapping": "v1"}),
    )
    return load_case(save_case(case, tmp_path / "batch").parent)


def test_a_pack_loads_its_facts_and_checks_in_order(tmp_path: Path) -> None:
    """Facts are named; checks keep the file's order, which is the order they apply in."""
    pack = load_pack(write_pack(tmp_path, WITH_CHECKS))
    assert sorted(pack.facts) == ["feed_error", "mapping_changed_message"]
    assert [rule.name for rule in pack.checks] == [
        "mapping-needs-a-change",
        "feed-error-is-not-mapping",
    ]
    first, second = pack.checks
    assert (first.refuse, first.fact, first.when) == (
        "FIX_MAPPING",
        "mapping_changed_message",
        False,
    )
    assert (second.refuse, second.fact, second.when) == ("FIX_MAPPING", "feed_error", True)


def test_the_rf_pack_has_its_five_checks_in_their_old_order() -> None:
    """The RF checks apply in the order the code applied them before they moved."""
    assert [rule.name for rule in DEFAULT_PACK.checks] == [
        "foreign-release",
        "firmware-unchanged",
        "timeout-with-instrument-error",
        "alignment-without-signal",
        "alignment-without-level-reading",
    ]


BROKEN_CHECKS = {
    "unknown fact kind": WITH_CHECKS.replace('kind = "errors_reported"', 'kind = "psychic"'),
    "fact not a table": WITH_CHECKS.replace(
        '[facts.feed_error]\nkind = "errors_reported"',
        "[facts]\nfeed_error = 3",
    ),
    "fact missing a parameter": WITH_CHECKS.replace('text = "mapping v2 deployed"', ""),
    "refuses an action the pack lacks": WITH_CHECKS.replace(
        'refuse = "FIX_MAPPING"\nwhen', 'refuse = "REBOOT"\nwhen'
    ),
    "refuses UNKNOWN": WITH_CHECKS.replace(
        'refuse = "FIX_MAPPING"\nwhen', 'refuse = "UNKNOWN"\nwhen'
    ),
    "names no fact": WITH_CHECKS.replace('when = "feed_error"', ""),
    "both when and unless": WITH_CHECKS.replace(
        'when = "feed_error"', 'when = "feed_error"\nunless = "feed_error"'
    ),
    "unknown fact": WITH_CHECKS.replace('when = "feed_error"', 'when = "weather"'),
    "reason quotes a value the fact lacks": WITH_CHECKS.replace("{error}", "{amount}"),
    "unless-check quotes a value": WITH_CHECKS.replace(
        '"no mapping change was deployed"', '"seen {count} times"'
    ),
    "repeated check name": WITH_CHECKS.replace(
        'name = "feed-error-is-not-mapping"', 'name = "mapping-needs-a-change"'
    ),
}
"""Packs whose facts or checks have one mistake each."""


@pytest.mark.parametrize("problem", sorted(BROKEN_CHECKS))
def test_a_broken_fact_or_check_is_refused_with_one_line(tmp_path: Path, problem: str) -> None:
    """Each mistake is refused at load, naming the file and the fact or check."""
    path = write_pack(tmp_path, BROKEN_CHECKS[problem])
    with pytest.raises(PackError) as refused:
        load_pack(path)
    assert str(path) in str(refused.value)
    assert "\n" not in str(refused.value)


def test_a_pack_needs_no_facts_or_checks(tmp_path: Path) -> None:
    """A new domain can start with actions only and add checks once it has measured a need."""
    pack = load_pack(write_pack(tmp_path, VALID))
    assert not pack.facts
    assert not pack.checks


def test_the_rf_level_units_agree_between_the_check_and_the_note() -> None:
    """The alignment check and the focused note must mean the same thing by a level."""
    check_fact = DEFAULT_PACK.facts["level_reading_failed"]
    note_fact = DEFAULT_PACK.facts["only_other_readings_failed"]
    assert isinstance(check_fact, ReadingFailedIn)
    assert isinstance(note_fact, ReadingsFailedOnlyOutside)
    assert set(check_fact.units) == set(note_fact.units)


def test_the_rf_evidence_wording_comes_from_its_pack() -> None:
    """The RF pack states versions, errors and its two notes in its own words."""
    wording = DEFAULT_PACK.evidence
    assert wording.versions_fact == "firmware_unchanged"
    assert wording.versions_changed.startswith("Firmware:")
    assert wording.logs == "Instrument logs"
    assert [(note.name, note.place) for note in wording.notes] == [
        ("no_signal", "lead"),
        ("no_level_reading", "focus"),
    ]


def test_a_pack_without_evidence_wording_gets_neutral_words(tmp_path: Path) -> None:
    """A new domain needn't write any: versions, errors and logs get plain defaults."""
    wording = load_pack(write_pack(tmp_path, VALID)).evidence
    assert wording.versions_fact is None
    assert "firmware" not in f"{wording.versions_changed} {wording.errors}".lower()
    assert wording.logs == "Logs"
    assert not wording.notes


WITH_EVIDENCE = (
    WITH_CHECKS
    + """
[evidence]
logs = "Gateway logs"

[[evidence.notes]]
name = "mapping_deployed"
fact = "mapping_changed_message"
place = "lead"
say = "Mapping change: logged {times}."
"""
)
"""The payments pack with evidence wording of its own."""


BROKEN_EVIDENCE = {
    "note on an unknown fact": WITH_EVIDENCE.replace(
        'fact = "mapping_changed_message"', 'fact = "weather"'
    ),
    "note in an unknown place": WITH_EVIDENCE.replace('place = "lead"', 'place = "footer"'),
    "note quotes a value its fact lacks": WITH_EVIDENCE.replace("{times}", "{amount}"),
    "note named like the versions line": WITH_EVIDENCE.replace(
        'name = "mapping_deployed"', 'name = "versions"'
    ),
    "versions fact of the wrong kind": WITH_EVIDENCE.replace(
        'logs = "Gateway logs"', 'logs = "Gateway logs"\nversions_fact = "feed_error"'
    ),
    "positional field": WITH_EVIDENCE.replace("{times}", "{}"),
    "unbalanced brace": WITH_EVIDENCE.replace("{times}", "{times"),
    "numbered field": WITH_EVIDENCE.replace("{times}", "{0}"),
    "nested field in a format spec": WITH_EVIDENCE.replace("{times}", "{times:{width}}"),
    "attribute of a field": WITH_EVIDENCE.replace("{times}", "{times.real}"),
    "check reason with a positional field": WITH_EVIDENCE.replace("{error}", "{}"),
    "unknown evidence entry": WITH_EVIDENCE.replace(
        'logs = "Gateway logs"', 'logs = "Gateway logs"\nfooter = "x"'
    ),
    "template quotes an unknown value": WITH_EVIDENCE.replace(
        'logs = "Gateway logs"', 'logs = "Gateway logs"\nerrors = "Errors: {amount}"'
    ),
}
"""Packs whose evidence wording has one mistake each."""


@pytest.mark.parametrize("problem", sorted(BROKEN_EVIDENCE))
def test_broken_evidence_wording_is_refused_with_one_line(tmp_path: Path, problem: str) -> None:
    """Each mistake is refused at load, naming the file."""
    path = write_pack(tmp_path, BROKEN_EVIDENCE[problem])
    with pytest.raises(PackError) as refused:
        load_pack(path)
    assert str(path) in str(refused.value)
    assert "\n" not in str(refused.value)


WRONG_SHAPES = {
    "facts as a list": ("top", "facts = []"),
    "checks as a table": ("top", "checks = {x = 1}"),
    "evidence as a list": ("top", "evidence = []"),
    "notes as text": ("top", 'evidence = {notes = "x"}'),
    "a fact entry not a table": ("top", "facts = {x = 1}"),
    "a check entry not a table": ("top", "checks = [1]"),
    "an action entry not a table": ("actions", 'actions = ["CABLE"]'),
    "actions as text": ("actions", 'actions = "CABLE"'),
}
"""Sections and entries of the wrong TOML type, each valid TOML: either an extra top-level
key, or the actions written as something other than [[actions]] tables."""


@pytest.mark.parametrize("problem", sorted(WRONG_SHAPES))
def test_a_section_of_the_wrong_type_is_a_pack_error(tmp_path: Path, problem: str) -> None:
    """Valid TOML with the wrong shape is refused as a pack error, never an AttributeError."""
    where, line = WRONG_SHAPES[problem]
    head = VALID.split("[[actions]]", maxsplit=1)[0]
    text = f"{line}\n{VALID}" if where == "top" else f"{head}{line}\n"
    with pytest.raises(PackError):
        load_pack(write_pack(tmp_path, text))
