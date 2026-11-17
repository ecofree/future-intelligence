"""Deterministic scoring. Preserve probabilities; never silently clip inputs."""
from __future__ import annotations

import math
from collections.abc import Sequence


def probability(value: float) -> float:
    """Validate an actual numeric probability, rejecting bool and non-finite data."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("probability must be a finite number")
    p = float(value)
    if not math.isfinite(p) or not 0.0 <= p <= 1.0:
        raise ValueError("probability must be between 0 and 1")
    return p


def outcome(value: int) -> int:
    if type(value) is not int or value not in (0, 1):
        raise ValueError("binary outcome must be integer 0 or 1")
    return value


def brier_score(p: float, y: int) -> float:
    """Binary, unscaled Brier score in [0, 1]; smaller is better."""
    return (probability(p) - outcome(y)) ** 2


def log_loss(p: float, y: int) -> float:
    """Natural-log loss. A wrong deterministic prediction receives infinity."""
    p, y = probability(p), outcome(y)
    if y == 1:
        return math.inf if p == 0 else -math.log(p)
    return math.inf if p == 1 else -math.log1p(-p)


def ranked_probability_score(probabilities: Sequence[float], observed_bin: int) -> float:
    """Normalized RPS over K ordered bins, dividing the K-1 errors by K-1.

    The bin boundaries and ordering MUST be frozen separately in the question.
    """
    values = [probability(p) for p in probabilities]
    n = len(values)
    if n < 2:
        raise ValueError("RPS requires at least two ordered bins")
    if type(observed_bin) is not int or not 0 <= observed_bin < n:
        raise ValueError("observed_bin is out of range")
    if not math.isclose(math.fsum(values), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("probabilities must sum to one")
    cdf = 0.0
    errors = []
    for index, p in enumerate(values[:-1]):
        cdf += p
        target = 1.0 if observed_bin <= index else 0.0
        errors.append((cdf - target) ** 2)
    return math.fsum(errors) / (n - 1)
