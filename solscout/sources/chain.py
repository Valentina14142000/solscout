from __future__ import annotations

from dataclasses import replace

from ..models import Safety


class ChainedSafety:
    """Try each safety source in order; the first one that can actually verify the token wins."""

    def __init__(self, checkers: list):
        self.checkers = checkers

    def check(self, mint: str) -> Safety:
        failures: list[Safety] = []
        for c in self.checkers:
            s = c.check(mint)
            if s.checked:
                return s
            failures.append(s)
        errors = "; ".join(f.error for f in failures if f.error) or "no safety source available"
        return Safety(error=errors[:160], retry_later=all(f.retry_later for f in failures))
