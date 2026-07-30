"""Unit tests for config/scoring_policy.yaml loading/validation
(scoring/policy.py) — CLAUDE.md §23 "fails loudly, not silently".
"""

from __future__ import annotations

import pytest

from trustsphere.scoring.policy import ScoringPolicy


def _minimal_valid_policy_dict(weight_a: float = 0.5, weight_b: float = 0.5) -> dict:
    return {
        "policy_version": "test-1",
        "factors": {
            "a": {"weight": weight_a, "description": "d", "source": "s"},
            "b": {"weight": weight_b, "description": "d", "source": "s"},
        },
        "normalisation": {"a": {}, "b": {}},
        "tiers": [
            {"tier": "HIGH", "min_score": 50},
            {"tier": "LOW", "min_score": 0},
        ],
        "hard_overrides": [],
        "complexity": {
            "weights": {"entity_count": 1},
            "bands": [{"band": "LOW", "max_points": 5}],
        },
        "queue": {"ageing_safeguard_days": 10, "qc_sample_rate": 0.02},
    }


def test_weights_must_sum_to_one():
    ScoringPolicy.model_validate(_minimal_valid_policy_dict(0.5, 0.5))  # should not raise


def test_weights_summing_to_less_than_one_raises():
    with pytest.raises(ValueError, match="must sum to 1.0"):
        ScoringPolicy.model_validate(_minimal_valid_policy_dict(0.3, 0.3))


def test_weights_summing_to_more_than_one_raises():
    with pytest.raises(ValueError, match="must sum to 1.0"):
        ScoringPolicy.model_validate(_minimal_valid_policy_dict(0.7, 0.7))


def test_real_policy_file_loads_and_weights_sum_to_one(policy):
    total = sum(f.weight for f in policy.factors.values())
    assert abs(total - 1.0) < 1e-6


def test_real_policy_file_hard_overrides_have_valid_forced_tiers(policy):
    valid_tiers = {t.tier for t in policy.tiers}
    for ov in policy.hard_overrides:
        assert ov.forced_tier in valid_tiers, ov.code


def test_tier_for_score_falls_back_to_lowest_tier_below_all_thresholds():
    p = ScoringPolicy.model_validate(_minimal_valid_policy_dict())
    assert p.tier_for_score(-100) == "LOW"
