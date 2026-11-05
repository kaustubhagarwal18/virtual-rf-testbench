"""The pipeline, in one place: evidence, then a prompt, then an answer.

Read this file to see what triage does. Everything it depends on is injected, so the same
pipeline serves every scenario: the evidence level decides how much the model is told, and
the sources decide whether it has any history to go on.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from triage.answer import MalformedAnswerError, Verdict, parse_verdict
from triage.evidence import ERROR_ONLY, FULL, SavedRun, build_evidence
from triage.llm import LLMProvider, ModelUnavailableError, OllamaProvider
from triage.prompt import Source, build_prompt


def triage_run(
    run: SavedRun,
    provider: LLMProvider,
    level: str = FULL,
    sources: Sequence[Source] = (),
) -> Verdict:
    """Decide what to do about one failed run.

    Args:
        run: The saved run, whose label is never read.
        provider: The answering model.
        level: How much evidence to give it: triage.evidence.ERROR_ONLY or FULL.
        sources: Past runs and documents to offer; none for the scenarios without retrieval.

    The model's reply is parsed strictly and not retried: a reply that can't be used raises
    MalformedAnswerError, and how often that happens is a result worth recording, not a thing
    to paper over.

    Returns:
        The verdict: the action to take, what to act on, why, and what it cited.
    """
    evidence = build_evidence(run, level)
    system, user = build_prompt(evidence, sources)
    reply = provider.complete(system, user)
    return parse_verdict(reply, [source.source_id for source in sources])


def main(arguments: Sequence[str] | None = None, provider: LLMProvider | None = None) -> int:
    """Triage one saved run from the command line and print the verdict.

    Args:
        arguments: The command-line arguments; None reads them from sys.argv.
        provider: The answering model; None uses Ollama.

    Returns:
        0 when a verdict was produced, 1 if the model couldn't be reached or answered
        unusably.
    """
    parser = argparse.ArgumentParser(
        prog="python -m triage.service", description="Explain one failed bench run."
    )
    parser.add_argument("run", type=Path, help="a saved run folder")
    parser.add_argument("--level", choices=(ERROR_ONLY, FULL), default=FULL, help="evidence level")
    parsed = parser.parse_args(arguments)
    provider = provider or OllamaProvider()
    try:
        verdict = triage_run(SavedRun.load(parsed.run), provider, parsed.level)
    except ModelUnavailableError as error:
        sys.stdout.write(f"{error}\n")
        return 1
    except MalformedAnswerError as error:
        sys.stdout.write(f"the model's answer couldn't be used: {error}\n")
        return 1
    sys.stdout.write(
        f"fix:   {verdict.fix.name} ({verdict.fix.value})\n"
        f"where: {verdict.where}\n"
        f"why:   {verdict.because}\n"
        f"cited: {', '.join(verdict.cited) or 'nothing'}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
