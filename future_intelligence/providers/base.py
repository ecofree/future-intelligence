"""Provider contracts only; no vendor has been connected by this initialization."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ForecastRequest:
    task_id: str
    question_version_id: str
    evidence_bundle_sha256: str
    model_id: str
    prompt: str
    deadline: datetime
    max_output_tokens: int
    budget_microusd: int

    def __post_init__(self) -> None:
        if self.deadline.tzinfo is None or self.deadline.utcoffset() is None:
            raise ValueError("deadline must be timezone aware")
        if (type(self.max_output_tokens) is not int or self.max_output_tokens < 1
                or type(self.budget_microusd) is not int or self.budget_microusd < 0):
            raise ValueError("output limit must be positive and budget nonnegative integers")


@dataclass(frozen=True)
class ForecastResponse:
    probability_ppm: int
    rationale: str
    actual_model_id: str
    provider_request_id: str
    input_tokens: int | None
    output_tokens: int | None
    cost_microusd: int | None
    raw_response: dict[str, Any]

    def __post_init__(self) -> None:
        if type(self.probability_ppm) is not int or not 0 <= self.probability_ppm <= 1_000_000:
            raise ValueError("probability_ppm must be an integer in [0, 1000000]")
        for name in ("input_tokens", "output_tokens", "cost_microusd"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a nonnegative integer or None (unknown)")


class ForecastProvider(ABC):
    """An adapter must implement real network behavior, limits and error mapping."""
    @abstractmethod
    async def forecast(self, request: ForecastRequest) -> ForecastResponse:
        raise NotImplementedError
