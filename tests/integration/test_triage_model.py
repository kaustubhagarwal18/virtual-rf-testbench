"""The one test that needs the real model: does qwen answer in the shape we demand?

Only the schema is checked, never the content: asserting on what a model says makes a test
that fails when it rephrases. Whether its answers are any good is measured by make eval,
not asserted here.

It skips when Ollama isn't running, so the suite stays runnable without it.
"""

from pathlib import Path

import pytest

from triage.answer import VERDICT_SCHEMA
from triage.bench_run import load_case
from triage.evidence import FULL
from triage.llm import OllamaProvider
from triage.pack import DEFAULT_PACK
from triage.service import triage_run

QUESTION_RUN = Path("triage/data/runs/questions/cable_loss-1")
"""One saved run, which the data set in the repository always has."""

pytestmark = pytest.mark.ollama


@pytest.fixture(name="provider")
def fixture_provider() -> OllamaProvider:
    """Return the local model, constrained to the answer's schema, or skip when it isn't running."""
    provider = OllamaProvider(response_format=VERDICT_SCHEMA)
    if not provider.available():
        pytest.skip("Ollama isn't running; start it with 'ollama serve'")
    return provider


def test_the_model_answers_in_the_shape_the_pipeline_demands(provider: OllamaProvider) -> None:
    """The reply parses, names an action from the list, and cites nothing it wasn't given."""
    verdict = triage_run(load_case(QUESTION_RUN), provider, FULL)
    assert verdict.fix in set(DEFAULT_PACK.actions)
    assert not verdict.cited
    assert verdict.because
