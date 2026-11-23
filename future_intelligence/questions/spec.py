"""Question definition v1. Dates must carry offsets; numbers use decimal strings."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REQUIRED = frozenset({
    "title", "event_definition", "domain", "track", "question_type", "event_cluster",
    "original_timezone", "forecast_deadline", "outcome_not_before", "resolution_deadline",
    "resolution_rule", "data_revision_policy", "unit", "bin_edges",
})
TIMES = ("forecast_deadline", "outcome_not_before", "resolution_deadline")
DOMAINS = {"MACRO", "POLICY"}


def validate_spec(value: Any) -> dict[str, Any]:
    """Return an isolated normalized document, rejecting unknown and ambiguous fields.

    This is client-side defense, not the security boundary. The database revalidates
    callers, including callers which bypass this module. No claim about actual
    absence of leaked outcomes is made by these schema/time checks.
    """
    if not isinstance(value, dict) or set(value) != REQUIRED:
        raise ValueError("question spec must contain exactly the v1 fields")
    spec = json.loads(json.dumps(value, allow_nan=False))
    for key in REQUIRED - {"bin_edges"}:
        if not isinstance(spec[key], str) or not spec[key].strip() or len(spec[key]) > 10000:
            raise ValueError(f"{key} must be a nonempty string of at most 10000 characters")
    if spec["domain"] not in DOMAINS or spec["track"] not in {"FAST", "STRATEGIC"}:
        raise ValueError("unsupported domain or track")
    if spec["question_type"] not in {"binary", "binned_numeric"}:
        raise ValueError("unsupported question type")
    if spec["data_revision_policy"] not in {"FIRST_RELEASE", "LATEST_AT_RESOLUTION_DEADLINE"}:
        raise ValueError("unsupported data revision policy")
    try:
        ZoneInfo(spec["original_timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("original_timezone must be an IANA time zone") from exc
    dates = []
    for key in TIMES:
        try:
            date = datetime.fromisoformat(spec[key])
        except ValueError as exc:
            raise ValueError(f"invalid {key}") from exc
        if date.tzinfo is None or date.utcoffset() is None:
            raise ValueError(f"{key} requires a UTC offset")
        date = date.astimezone(timezone.utc)
        dates.append(date)
        spec[key] = date.isoformat(timespec="microseconds")
    # Forecast admission is exclusive; outcome publication is inclusive. Their
    # bounds may coincide without admitting a prediction at the release instant.
    if not dates[0] <= dates[1] <= dates[2]:
        raise ValueError("require forecast_deadline <= outcome_not_before <= resolution_deadline")
    edges = spec["bin_edges"]
    if not isinstance(edges, list) or len(edges) > 100:
        raise ValueError("bin_edges must be a list with at most 100 entries")
    if spec["question_type"] == "binary" and edges:
        raise ValueError("binary question cannot contain bin edges")
    if spec["question_type"] == "binned_numeric" and not edges:
        raise ValueError("numeric question needs at least one boundary")
    previous = None
    for edge in edges:
        if not isinstance(edge, str) or len(edge) > 100 or not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", edge):
            raise ValueError("bin boundaries must be decimal strings")
        try:
            number = Decimal(edge)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal boundary") from exc
        if not number.is_finite() or (previous is not None and number <= previous):
            raise ValueError("boundaries must be finite and strictly increasing")
        previous = number
    return spec


def outcome_bin(value: str, edges: list[str]) -> int:
    """Bins are [-inf,e0), [e0,e1), ... [last,+inf); not a resolver."""
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("value must be a finite decimal string")
    try:
        number = Decimal(value)
        bounds = [Decimal(edge) for edge in edges]
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid decimal") from exc
    if not number.is_finite() or not bounds or any(not x.is_finite() for x in bounds):
        raise ValueError("nonfinite or missing boundaries")
    if any(a >= b for a, b in zip(bounds, bounds[1:])):
        raise ValueError("boundaries must increase")
    return sum(number >= bound for bound in bounds)


def answer_contract(spec: dict[str, Any]) -> dict[str, Any]:
    """Explicit model answer space, derived only from the frozen question v1.

    This does not change outcome_bin or score semantics. Null endpoints denote
    unbounded intervals; finite lower bounds are included, upper bounds excluded.
    """
    value = validate_spec(spec)
    if value['question_type'] == 'binary':
        categories = [{'index': 0, 'label': 'No'}, {'index': 1, 'label': 'Yes'}]
    else:
        edges = value['bin_edges']
        categories = []
        for index in range(len(edges) + 1):
            lower = edges[index - 1] if index else None
            upper = edges[index] if index < len(edges) else None
            label = (f'x < {upper}' if lower is None else
                     f'x >= {lower}' if upper is None else f'{lower} <= x < {upper}')
            categories.append({'index': index, 'label': label, 'lower': lower,
                               'upper': upper, 'lower_inclusive': lower is not None,
                               'upper_inclusive': False})
    return {'protocol': 'fi.answer-contract.v2', 'category_count': len(categories),
            'probability_total_ppm': 1000000, 'unit': value['unit'],
            'categories': categories}
