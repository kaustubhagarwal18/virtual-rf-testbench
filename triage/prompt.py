"""The prompt the answering model sees; the actions it may choose come from the domain pack.

Every scenario uses this one builder. The baseline passes no sources and the retrieval
scenario passes the past runs it found; nothing else differs. If the instructions or the
schema changed between scenarios, the measured difference would include my prompt edits
rather than what retrieval adds.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from triage.pack import DEFAULT_PACK, Pack, system_prompt


@dataclass(frozen=True)
class Source:
    """One past run or document offered to the model.

    Attributes:
        source_id: What the model must cite to use it. A neutral id that says nothing about
            the run, so the model can't read the answer off the name.
        text: What it says, including how it was resolved.
        fault: The labelled fault of a past run, for scoring and for holding a fault out of
            retrieval; empty when unknown. Never put in a prompt.
        action: The key of the action that resolved a past run, as its label records it;
            empty when not recorded. Read by the abstention gates, not by the prompt builder
            (the source's text states it already).
        similarity: How alike this source and the run being triaged are, as the retriever
            scored them (cosine, 1 is identical); None when the retriever doesn't say.
        fix_applied: What fixed this past run, as its label records it, so an answer citing
            it can quote it word for word; empty when not recorded. The text states it too.
        symptom_text: The run's evidence alone, without its recorded cause and fix, so an
            index can match symptoms to symptoms; empty when the source has no such split.
    """

    source_id: str
    text: str
    fault: str = ""
    action: str = ""
    similarity: float | None = None
    fix_applied: str = ""
    symptom_text: str = ""


SYSTEM_PROMPT = system_prompt(DEFAULT_PACK)
"""The instructions for the default pack, identical in every scenario."""


def build_prompt(
    evidence: str, sources: Sequence[Source] = (), pack: Pack = DEFAULT_PACK
) -> tuple[str, str]:
    """Build the system and user messages for one question.

    Args:
        evidence: The run's evidence, from triage.evidence.
        sources: Past runs and documents retrieval found; none for the baseline scenarios.
        pack: The domain pack, which describes the domain and lists the actions.

    Returns:
        The system prompt and the user prompt.
    """
    parts = [f"EVIDENCE\n{evidence}"]
    if sources:
        listed = "\n\n".join(f"[{source.source_id}]\n{source.text}" for source in sources)
        parts.append(f"SOURCES\n{listed}")
    else:
        parts.append("SOURCES\nNone. Answer from the evidence alone, and cite nothing.")
    system = SYSTEM_PROMPT if pack == DEFAULT_PACK else system_prompt(pack)
    return system, "\n\n".join(parts)
