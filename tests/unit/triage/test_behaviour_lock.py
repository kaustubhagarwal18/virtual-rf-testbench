"""A lock on everything triage shows the model and scores, so a refactor can prove it changed none.

Moving the domain knowledge out of the code (the action list, the facts, the checks) must
not change one prompt: the evaluation's numbers, and its cached answers, hold only while the
model sees the same text. A full `make eval` needs a model and an embedder; this test needs
neither. It hashes the system prompt, the answer schema, the evidence of every recorded run
at every level, every past run's text, a prompt with past runs for every question, the
checks' verdict on every action for every question, the majority vote, and the parsing of
every action, and compares them with the hashes recorded before the refactor.

Run with UPDATE_BEHAVIOUR_LOCK=1 to record new hashes, only for a change meant to alter
behaviour, and say so in the commit.
"""

import hashlib
import json
import os
from pathlib import Path

from triage.abstain import DEFAULT_GATES
from triage.answer import VERDICT_SCHEMA, Verdict, parse_verdict, verdict_schema
from triage.checks import DEFAULT_CHECKS, apply_checks
from triage.evidence import EVALUATED_LEVELS, SavedRun, build_evidence
from triage.label import load_label
from triage.prompt import SYSTEM_PROMPT, Fix, build_prompt, fix_category
from triage.retrieval import history_source
from triage.vote import vote_run

RUNS = Path("triage/data/runs")
LOCK_FILE = Path(__file__).with_name("behaviour_lock.json")
UPDATE_VARIABLE = "UPDATE_BEHAVIOUR_LOCK"


def action_keys() -> list[str]:
    """Return every action's key, UNKNOWN included, in the order the prompt lists them."""
    return [fix.name for fix in Fix]


def action(key: str) -> Fix:
    """Return the action with this key."""
    return Fix[key]


def action_text(key: str) -> str:
    """Return what the action with this key says to do."""
    return action(key).value


def digest(value: object) -> str:
    """Hash a value's canonical JSON, so the lock file stays small and diffs name the part."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def runs(kind: str) -> list[SavedRun]:
    """Load every recorded run of one kind, history or questions, in name order."""
    return [SavedRun.load(folder) for folder in sorted((RUNS / kind).iterdir()) if folder.is_dir()]


def reviewed(verdict: Verdict) -> dict[str, object]:
    """Describe a reviewed verdict by the fields a reader or the scorer sees."""
    return {
        "fix": verdict.fix.name,
        "category": verdict.category,
        "overruled": verdict.overruled,
        "gate_reason": verdict.gate_reason,
        "because": verdict.because,
    }


def fingerprint() -> dict[str, str]:
    """Hash every part of triage's behaviour that a refactor must leave alone."""
    history = runs("history")
    questions = runs("questions")
    sources = [history_source(run, load_label(run)) for run in history]
    by_fault = {
        fault: [source for source in sources if source.fault == fault]
        for fault in {source.fault for source in sources}
    }
    parts: dict[str, object] = {
        "system prompt": SYSTEM_PROMPT,
        "answer schema": [VERDICT_SCHEMA, verdict_schema(["past-1", "past-2"])],
        "actions": [[key, action_text(key), fix_category(action(key))] for key in action_keys()],
        "parsing": [
            reviewed(parse_verdict(json.dumps({"fix": reply, "where": "w", "because": "b"})))
            for key in action_keys()
            for reply in (key, key.lower(), action_text(key))
        ],
    }
    for run in [*history, *questions]:
        name = f"{run.folder.parent.name}/{run.folder.name}"
        parts[f"evidence {name}"] = [build_evidence(run, level) for level in EVALUATED_LEVELS]
    for source in sources:
        parts[f"source {source.source_id}"] = [
            source.text,
            source.fault,
            source.action,
            source.fix_applied,
        ]
    for run in questions:
        own = by_fault[str(load_label(run)["fault"])]
        parts[f"prompt {run.folder.name}"] = build_prompt(build_evidence(run), own)
        parts[f"checks {run.folder.name}"] = [
            reviewed(apply_checks(run, Verdict(action(key), "w", "b", []), DEFAULT_CHECKS))
            for key in action_keys()
        ]
        parts[f"vote {run.folder.name}"] = [
            reviewed(vote_run(run, own[:3], checks=DEFAULT_CHECKS, gates=DEFAULT_GATES)),
            reviewed(vote_run(run, sources[:3], checks=DEFAULT_CHECKS, gates=DEFAULT_GATES)),
        ]
    return {name: digest(value) for name, value in sorted(parts.items())}


def test_triage_behaves_exactly_as_recorded() -> None:
    """Every prompt, schema, evidence text, source, check and vote hashes as before."""
    current = fingerprint()
    if os.environ.get(UPDATE_VARIABLE):
        LOCK_FILE.write_text(json.dumps(current, indent=1) + "\n", encoding="utf-8")
    recorded = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    changed = sorted(
        name for name in recorded.keys() | current.keys() if recorded.get(name) != current.get(name)
    )
    assert not changed, f"behaviour changed in: {changed[:10]}"
