"""Small text helpers shared by the triage reports.

The evaluation report and the retrieval demo both shorten a model's reason or a failure
message to keep a table row readable. That shortening lives here, in one place, so the two
reports clip the same way.
"""


def clip(text: str, limit: int) -> str:
    """Shorten text to a limit at a word boundary, collapsing whitespace first.

    Args:
        text: The text to shorten; runs of whitespace, including newlines, are collapsed to
            single spaces so a multi-line value becomes one line.
        limit: The longest result, in characters, before the ellipsis.

    Returns:
        The collapsed text when it fits within the limit, or its first whole words within the
        limit followed by an ellipsis when it is longer, so no word is cut mid-way.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit].rsplit(' ', 1)[0]}..."
