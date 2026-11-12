"""The pipeline, in one place: evidence, then a prompt, then an answer.

Read this file to see what triage does. Everything it depends on is injected, so the same
pipeline serves every scenario: the evidence level decides how much the model is told, and
the sources decide whether it has any history to go on.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from triage.abstain import SourceGate, reason_to_abstain
from triage.answer import (
    MalformedAnswerError,
    Verdict,
    parse_verdict,
    past_fix_lines,
    verdict_schema,
    with_past_fixes,
)
from triage.bench_run import load_case
from triage.case import Case, CaseError
from triage.checks import VerdictCheck, apply_checks, checks_for
from triage.evidence import ERROR_ONLY, FULL, build_evidence
from triage.llm import LLMProvider, ModelUnavailableError, OllamaProvider
from triage.pack import DEFAULT_PACK, DEFAULT_PACK_FILE, Pack, PackError, load_pack
from triage.prompt import Source, build_prompt


def _schema_for(source_ids: Sequence[str], pack: Pack) -> dict[str, object] | None:
    """Choose the schema to send with a question, or None for the provider's own.

    The provider's own schema is the default pack's, so another pack always sends its own,
    and a question with sources sends one narrowed to their ids.

    Args:
        source_ids: The ids of the sources offered with the question.
        pack: The domain pack.

    Returns:
        The schema, or None when the provider's own fits.
    """
    if not source_ids and pack == DEFAULT_PACK:
        return None
    return verdict_schema(source_ids, pack)


def triage_run(
    run: Case,
    provider: LLMProvider,
    level: str = FULL,
    sources: Sequence[Source] = (),
    *,
    checks: tuple[VerdictCheck, ...] = (),
    gates: Sequence[SourceGate] = (),
    pack: Pack = DEFAULT_PACK,
) -> Verdict:
    """Decide what to do about one failed run.

    The model's reply is parsed strictly and not retried: a reply that can't be used raises
    MalformedAnswerError, and how often that happens is a result worth recording, not a thing
    to paper over.

    Args:
        run: The saved run, whose label is never read.
        provider: The answering model.
        level: How much evidence to give it: triage.evidence.ERROR_ONLY or FULL.
        sources: Past runs and documents to offer; none for the scenarios without retrieval.
        checks: Rules the verdict must agree with; one that contradicts the evidence becomes
            UNKNOWN. None by default, so a measurement of the model alone stays one.
        gates: Rules on the sources alone; when one finds no basis for an answer, the
            verdict is UNKNOWN and the model is not asked. None by default.
        pack: The domain pack: its description and actions shape the prompt, and the
            reply must name one of its actions. Its checks apply only if passed as checks.

    Returns:
        The verdict: the action to take, what to act on, why, and what it cited.
    """
    reason = reason_to_abstain(sources, gates)
    if reason:
        return Verdict.gated(reason)
    evidence = build_evidence(run, level, pack)
    system, user = build_prompt(evidence, sources, pack)
    source_ids = [source.source_id for source in sources]
    reply = provider.complete(system, user, _schema_for(source_ids, pack))
    verdict = parse_verdict(reply, source_ids, pack)
    return with_past_fixes(apply_checks(run, verdict, checks), sources)


def describe(verdict: Verdict) -> str:
    """Format a verdict as the lines the command line prints.

    Args:
        verdict: The verdict.

    Returns:
        The fix, where, why, whether that is the firmware, the bench or the test, what was
        cited and what fixed the past runs behind it last time, plus why a check overruled the model
        if one did.
    """
    lines = [
        f"fix:   {verdict.fix.key} ({verdict.fix.says})",
        f"where: {verdict.where}",
        f"why:   {verdict.because}",
        f"cause: {verdict.category}",
        f"cited: {', '.join(verdict.cited) or 'nothing'}",
    ]
    lines += [f"last time: {line}" for line in past_fix_lines(verdict)]
    if verdict.overruled:
        lines.append(f"note:  {verdict.overruled}")
    return "\n".join(lines) + "\n"


def main(arguments: Sequence[str] | None = None, provider: LLMProvider | None = None) -> int:
    """Triage one saved run from the command line and print the verdict.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        provider: The answering model; None uses Ollama.

    Returns:
        0 when a verdict was produced, 1 if the pack or the case can't be used, or the
        model couldn't be reached or answered unusably.
    """
    parser = argparse.ArgumentParser(
        prog="python -m triage.service", description="Explain one failed bench run."
    )
    parser.add_argument("run", type=Path, help="a saved run folder")
    parser.add_argument("--level", choices=(ERROR_ONLY, FULL), default=FULL, help="evidence level")
    parser.add_argument(
        "--pack", type=Path, default=DEFAULT_PACK_FILE, help="domain pack (default: the RF bench)"
    )
    parsed = parser.parse_args(arguments)
    try:
        pack = load_pack(parsed.pack)
    except PackError as error:
        sys.stdout.write(f"{error}\n")
        return 1
    provider = (
        provider
        if provider is not None
        else OllamaProvider(response_format=verdict_schema(pack=pack))
    )
    try:
        verdict = triage_run(
            load_case(parsed.run), provider, parsed.level, checks=checks_for(pack), pack=pack
        )
    except (ModelUnavailableError, CaseError, FileNotFoundError) as error:
        sys.stdout.write(f"{error}\n")
        return 1
    except MalformedAnswerError as error:
        sys.stdout.write(f"the model's answer couldn't be used: {error}\n")
        return 1
    sys.stdout.write(describe(verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
