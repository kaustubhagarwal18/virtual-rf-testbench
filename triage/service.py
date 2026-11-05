"""The pipeline, in one place: evidence, then a prompt, then an answer.

Read this file to see what triage does. Everything it depends on is injected, so the same
pipeline serves every scenario: the evidence level decides how much the model is told, and
the sources decide whether it has any history to go on.
"""

from collections.abc import Sequence

from triage.answer import Verdict, parse_verdict
from triage.evidence import FULL, SavedRun, build_evidence
from triage.llm import LLMProvider
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

    Returns:
        The verdict: the action to take, what to act on, why, and what it cited.

    Raises:
        MalformedAnswerError: If the model's reply can't be used. It is deliberately not
            retried: how often that happens is a result worth recording.
    """
    evidence = build_evidence(run, level)
    system, user = build_prompt(evidence, sources)
    reply = provider.complete(system, user)
    return parse_verdict(reply, [source.source_id for source in sources])
