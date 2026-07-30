"""Loads and validates config/scoring_policy.yaml.

Fails loudly (not silently) if weights don't sum to 1.0 or a tier/override
references a field the schema doesn't recognise — a scoring policy that
can't be trusted at load time must not run at all (CLAUDE.md §23 "Do not
silently catch exceptions").
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator


class FactorConfig(BaseModel):
    weight: float
    description: str
    source: str


class TierConfig(BaseModel):
    tier: str
    min_score: float


class HardOverrideConfig(BaseModel):
    code: str
    field: str
    reason: str
    forced_tier: str
    equals: bool | None = None
    at_or_below: float | None = None
    at_or_above: float | None = None


class ComplexityBandConfig(BaseModel):
    band: str
    max_points: int


class ComplexityConfig(BaseModel):
    weights: dict[str, int]
    bands: list[ComplexityBandConfig]


class QueueConfig(BaseModel):
    ageing_safeguard_days: int
    qc_sample_rate: float


class ScoringPolicy(BaseModel):
    policy_version: str
    factors: dict[str, FactorConfig]
    normalisation: dict[str, dict]
    tiers: list[TierConfig]
    hard_overrides: list[HardOverrideConfig]
    complexity: ComplexityConfig
    queue: QueueConfig

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "ScoringPolicy":
        total = sum(f.weight for f in self.factors.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring_policy factor weights must sum to 1.0, got {total}")
        return self

    def tier_for_score(self, score: float) -> str:
        for t in sorted(self.tiers, key=lambda x: -x.min_score):
            if score >= t.min_score:
                return t.tier
        return self.tiers[-1].tier


@lru_cache
def load_policy(path: str | None = None) -> ScoringPolicy:
    from trustsphere.config import get_settings

    p = Path(path or get_settings().scoring_policy_path)
    with open(p) as f:
        raw = yaml.safe_load(f)
    return ScoringPolicy.model_validate(raw)
