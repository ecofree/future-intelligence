"""Versioned, bounded experiment configuration; PostgreSQL also validates it."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import re
from uuid import UUID

SPEC_KEYS = {
    "protocol", "suite", "run_kind", "forecast_deadline", "budget_microusd",
    "attempt_cap_microusd", "max_attempts", "lease_seconds", "failure_policy",
    "baseline_policy", "disclosure_policy",
}
ENTRY_KEYS = {"entry_key", "provider", "model_id", "prompt_sha256", "parameters"}
DEADLINE_RE = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"


def validate_registration(versions: list[str], entries: list[dict], spec: dict) -> tuple:
    """Return isolated, sorted input. Do not compare client clocks with cutoffs."""
    if not isinstance(spec, dict) or set(spec) != SPEC_KEYS:
        raise ValueError("experiment specification keys must match fi.experiment.v1")
    fixed = {"protocol": "fi.experiment.v1", "suite": "A",
             "failure_policy": "REPORT_MISSING", "baseline_policy": "UNIFORM_CATEGORIES_V1",
             "disclosure_policy": "COMPLETE_BATCH_AFTER_RESOLUTION"}
    if any(spec[k] != value for k, value in fixed.items()):
        raise ValueError("unsupported experiment protocol or policy")
    if spec["run_kind"] not in ("FIXTURE", "LIVE"):
        raise ValueError("run_kind must be FIXTURE or LIVE")
    for key, maximum in {"budget_microusd": 10**12, "attempt_cap_microusd": 10**12,
                         "max_attempts": 5, "lease_seconds": 3600}.items():
        if type(spec[key]) is not int or not 1 <= spec[key] <= maximum:
            raise ValueError(f"{key} must be a bounded positive integer")
    if spec["attempt_cap_microusd"] > spec["budget_microusd"]:
        raise ValueError("attempt cap exceeds experiment budget")
    deadline = spec["forecast_deadline"]
    if not isinstance(deadline, str) or not re.fullmatch(DEADLINE_RE, deadline):
        raise ValueError("forecast_deadline must include an explicit UTC offset")
    datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    if not isinstance(versions, list) or not 1 <= len(versions) <= 500:
        raise ValueError("need 1-500 question versions")
    normalized = [str(UUID(v)) for v in versions]
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate question version")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 10:
        raise ValueError("need 1-10 entries")
    allowed = ("fixture",) if spec["run_kind"] == "FIXTURE" else ("openai", "anthropic", "google")
    names = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise ValueError("invalid entry keys")
        for key in ENTRY_KEYS - {"parameters"}:
            if not isinstance(entry[key], str) or not 1 <= len(entry[key].strip()) <= 200:
                raise ValueError(f"invalid entry field {key}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", entry["entry_key"]):
            raise ValueError("invalid entry key")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["prompt_sha256"]):
            raise ValueError("invalid prompt digest")
        if entry["provider"] not in allowed or not isinstance(entry["parameters"], dict):
            raise ValueError("provider/run_kind mismatch or invalid parameters")
        if any(k.lower() in {"api_key", "apikey", "authorization", "password", "secret", "token"}
               for k in entry["parameters"]):
            raise ValueError("credentials must not be included in model configuration")
        json.dumps(entry, allow_nan=False)
        names.append(entry["entry_key"])
    if len(set(names)) != len(names):
        raise ValueError("duplicate entry key")
    return sorted(normalized), deepcopy(sorted(entries, key=lambda e: e["entry_key"])), deepcopy(spec)


def uniform_ppm(categories: int) -> list[int]:
    """Deterministic integer allocation; baseline definition is separately registered."""
    if type(categories) is not int or not 2 <= categories <= 101:
        raise ValueError("categories must be 2-101")
    value, extra = divmod(1_000_000, categories)
    return [value + (i < extra) for i in range(categories)]
