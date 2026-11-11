"""Reading a saved run's label: its known cause and fix, kept apart from the evidence.

triage.evidence never reads the label, because the label is the answer. It is read only here,
for the two jobs that need it: scoring a question run (the answer key) and turning a history
run into a source the model may cite (how that past failure was resolved).
"""

import json
from typing import Any

from triage.case import Case

LABEL_FILE = "label.json"


def load_label(run: Case) -> dict[str, Any]:
    """Read a saved run's label.

    Args:
        run: The saved run; its folder holds the label beside the evidence.

    Returns:
        The label's fields, such as the fault, its cause and the fix that resolved it.
    """
    label: dict[str, Any] = json.loads((run.folder / LABEL_FILE).read_text(encoding="utf-8"))
    return label
