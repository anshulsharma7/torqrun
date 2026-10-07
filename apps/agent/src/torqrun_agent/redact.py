"""Mask secret values in job output before it leaves the agent."""

import base64
from collections.abc import Iterable

MASK = "***"
MIN_LENGTH = 3  # shorter values would mask ordinary text and reveal little anyway


class Redactor:
    """Replaces every occurrence of the given values (and their base64 encoding) with ``***``.

    Output is processed line by line, so a multi-line secret is matched per line. Like any
    log masking this is a safety net, not a guarantee: a job that transforms a secret (base64,
    reversing it, encrypting it…) can still print it. Don't print secrets.
    """

    def __init__(self, values: Iterable[str]) -> None:
        parts: set[str] = set()
        for v in values:
            if len(v.strip()) < MIN_LENGTH:
                continue
            b64 = base64.b64encode(v.encode()).decode()
            candidates = [v, *v.splitlines(), b64, b64.rstrip("=")]
            parts.update(p for p in candidates if len(p.strip()) >= MIN_LENGTH)
        # Longest first, so a secret containing another is masked whole.
        self._values = sorted(parts, key=len, reverse=True)

    def __bool__(self) -> bool:
        return bool(self._values)

    def __call__(self, text: str) -> str:
        for v in self._values:
            if v in text:
                text = text.replace(v, MASK)
        return text
