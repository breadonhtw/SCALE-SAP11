"""Unit tests for the pure deterministic scoring engine (A2 checklist item:
"Unit tests: factors, tier boundaries, overrides, queue ordering").

No DB, no HTTP, no vendor SDK — `score_alert` takes plain domain objects and
a `ScoringPolicy`, per CLAUDE.md §23 "keep domain logic independent of
vendor SDKs". Queue ordering itself is exercised at the persistence layer
(tests/integration/test_local_repository.py) since the ORDER BY lives in SQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trustsphere.domain.alerts import ComplexityBand, UrgencyTier
from trustsphere.scoring.engine import score_alert

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


# -- factors ------------------------------------------------------------------


def test_typology_severity_known_priorities_map_correctly(policy, base_inputs, base_complexity):
    expected = {"CRITICAL": 100, "HIGH": 80, "MEDIUM": 50, "LOW": 25}
    for priority, value in expected.items():
        inputs = base_inputs.model_copy(update={"alert_priority": priority})
        result = score_alert(inputs, base_complexity, policy, now=NOW)
        factor = next(f for f in result.factors if f.factor_code == "typology_severity")
        assert factor.normalised_value == value, priority


def test_typology_severity_missing_priority_uses_default(policy, base_inputs, base_complexity):
    inputs = base_inputs.model_copy(update={"alert_priority": None})
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    factor = next(f for f in result.factors if f.factor_code == "typology_severity")
    assert factor.normalised_value == policy.normalisation["typology_severity"]["default"]
    assert "not present" in factor.reason_code


def test_entity_risk_kyc_penalty_applied_and_capped(policy, base_inputs, base_complexity):
    inputs = base_inputs.model_copy(update={
        "composite_risk_score": Decimal("95"), "kyc_effective_status": "REJECTED",
    })
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    factor = next(f for f in result.factors if f.factor_code == "entity_risk")
    # 95 + 30 penalty would be 125, capped at 100.
    assert factor.normalised_value == 100.0


def test_entity_risk_missing_composite_uses_default(policy, base_inputs, base_complexity):
    inputs = base_inputs.model_copy(update={"composite_risk_score": None})
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    factor = next(f for f in result.factors if f.factor_code == "entity_risk")
    assert factor.normalised_value == policy.normalisation["entity_risk"]["default"]


def test_jurisdiction_exposure_tier_floor_and_cross_border_bonus(policy, base_inputs, base_complexity):
    inputs = base_inputs.model_copy(update={
        "country_risk_score": Decimal("10"), "risk_tier": "CRITICAL", "is_cross_border": True,
    })
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    factor = next(f for f in result.factors if f.factor_code == "jurisdiction_exposure")
    # floor(CRITICAL)=90, + cross_border_bonus=10 -> capped at 100.
    assert factor.normalised_value == 100.0


def test_sla_age_proximity_breakpoints(policy, base_inputs, base_complexity):
    cases = [(-2.0, 100), (12.0, 90), (48.0, 70), (150.0, 45), (200.0, 20)]
    for hours_remaining, expected in cases:
        inputs = base_inputs.model_copy(update={"hours_remaining_to_sla": hours_remaining})
        result = score_alert(inputs, base_complexity, policy, now=NOW)
        factor = next(f for f in result.factors if f.factor_code == "sla_age_proximity")
        assert factor.normalised_value == expected, hours_remaining


def test_sla_age_proximity_missing_uses_default(policy, base_inputs, base_complexity):
    inputs = base_inputs.model_copy(update={"hours_remaining_to_sla": None})
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    factor = next(f for f in result.factors if f.factor_code == "sla_age_proximity")
    assert factor.normalised_value == policy.normalisation["sla_age_proximity"]["default"]


def test_transaction_materiality_ratio_breakpoints(policy, base_inputs, base_complexity):
    cases = [(Decimal("18000"), 15), (Decimal("50000"), 45), (Decimal("140000"), 75), (Decimal("900000"), 100)]
    for amount, expected in cases:
        inputs = base_inputs.model_copy(update={
            "amount_usd": amount, "baseline_avg_amount_usd": Decimal("18000"),
        })
        result = score_alert(inputs, base_complexity, policy, now=NOW)
        factor = next(f for f in result.factors if f.factor_code == "transaction_materiality")
        assert factor.normalised_value == expected, amount


def test_transaction_materiality_missing_baseline_uses_default(policy, base_inputs, base_complexity):
    inputs = base_inputs.model_copy(update={"baseline_avg_amount_usd": None})
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    factor = next(f for f in result.factors if f.factor_code == "transaction_materiality")
    assert factor.normalised_value == policy.normalisation["transaction_materiality"]["default"]


def test_transaction_materiality_decimal_precision_not_lost(policy, base_inputs, base_complexity):
    """Decimal inputs must survive the ratio calculation without binary-float
    surprises (CLAUDE.md §7: never use binary floating point for stored
    financial amounts — the engine converts to float only for the
    normalisation curve, never for the persisted raw value).
    """
    inputs = base_inputs.model_copy(update={
        "amount_usd": Decimal("100000.55"), "baseline_avg_amount_usd": Decimal("10000.05"),
    })
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    factor = next(f for f in result.factors if f.factor_code == "transaction_materiality")
    assert factor.raw_value == "100000.55"  # verbatim, not recomputed/rounded


# -- urgency score & tier boundaries -------------------------------------------


def test_urgency_score_is_weighted_sum_of_factors(policy, base_inputs, base_complexity):
    result = score_alert(base_inputs, base_complexity, policy, now=NOW)
    expected = round(sum(f.weighted_points for f in result.factors), 2)
    assert result.urgency_score == expected


def test_tier_boundaries_are_inclusive_lower_bounds(policy):
    assert policy.tier_for_score(85.0) == "CRITICAL"
    assert policy.tier_for_score(84.99) == "HIGH"
    assert policy.tier_for_score(65.0) == "HIGH"
    assert policy.tier_for_score(64.99) == "MEDIUM"
    assert policy.tier_for_score(40.0) == "MEDIUM"
    assert policy.tier_for_score(39.99) == "LOW"
    assert policy.tier_for_score(0.0) == "LOW"


# -- hard overrides -------------------------------------------------------------


def test_sanctions_override_forces_critical_regardless_of_low_score(policy, base_inputs, base_complexity):
    inputs = base_inputs.model_copy(update={
        "alert_priority": "LOW", "composite_risk_score": Decimal("5"),
        "country_risk_score": Decimal("5"), "risk_tier": "LOW",
        "hours_remaining_to_sla": 500.0, "sanctions_match": True,
    })
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    assert result.urgency_tier == UrgencyTier.CRITICAL
    assert result.hard_override is not None
    assert result.hard_override.code == "SANCTIONS_MATCH"
    # The override does not erase the underlying factor breakdown (CLAUDE.md §9).
    assert len(result.factors) == 5


def test_no_override_when_field_unresolved(policy, base_inputs, base_complexity):
    """An override whose backing field is None (not yet confirmed against the
    live tenant) must never fire — a missing field is never guessed into a
    forced CRITICAL tier.
    """
    inputs = base_inputs.model_copy(update={"sanctions_match": None})
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    assert result.hard_override is None


def test_first_matching_override_wins(policy, base_inputs, base_complexity):
    """Both SANCTIONS_MATCH and RESTRICTED_CUSTOMER conditions are true;
    policy order (config/scoring_policy.yaml) says sanctions is evaluated
    first and must win.
    """
    inputs = base_inputs.model_copy(update={
        "sanctions_match": True, "restricted_customer_flag": True,
    })
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    assert result.hard_override.code == "SANCTIONS_MATCH"


def test_imminent_sla_breach_override(policy, base_inputs, base_complexity):
    inputs = base_inputs.model_copy(update={"hours_remaining_to_sla": 2.0})
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    assert result.hard_override.code == "IMMINENT_SLA_BREACH"
    assert result.urgency_tier == UrgencyTier.CRITICAL


def test_repeat_escalated_alert_override_forces_high_not_critical(policy, base_inputs, base_complexity):
    inputs = base_inputs.model_copy(update={"has_prior_escalated_case": True})
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    assert result.hard_override.code == "REPEAT_ESCALATED_ALERT"
    assert result.urgency_tier == UrgencyTier.HIGH


def test_clean_alert_has_no_override_and_uses_computed_tier(policy, base_inputs, base_complexity):
    result = score_alert(base_inputs, base_complexity, policy, now=NOW)
    assert result.hard_override is None
    assert result.urgency_tier == UrgencyTier(policy.tier_for_score(result.urgency_score))


# -- complexity is independent of urgency --------------------------------------


def test_complexity_does_not_affect_urgency_score(policy, base_inputs):
    from trustsphere.domain.alerts import ComplexityInputs

    low = ComplexityInputs(alert_id="a", entity_count=1, jurisdiction_count=0, missing_kyc_count=0,
                            source_system_count=1, related_alert_count=0, transaction_volume_band=0)
    high = ComplexityInputs(alert_id="a", entity_count=20, jurisdiction_count=8, missing_kyc_count=5,
                             source_system_count=3, related_alert_count=10, transaction_volume_band=3)
    result_low = score_alert(base_inputs, low, policy, now=NOW)
    result_high = score_alert(base_inputs, high, policy, now=NOW)
    assert result_low.urgency_score == result_high.urgency_score
    assert result_low.urgency_tier == result_high.urgency_tier
    assert result_low.complexity_band != result_high.complexity_band


def test_complexity_band_thresholds(policy, base_inputs):
    from trustsphere.domain.alerts import ComplexityInputs

    # weights: entity=1, jurisdiction=2, missing_kyc=2, source_system=1, related_alert=2, volume=1
    # bands: LOW<=4, MEDIUM<=9, HIGH<=15, VERY_HIGH>15
    low = ComplexityInputs(alert_id="a", entity_count=4, jurisdiction_count=0, missing_kyc_count=0,
                            source_system_count=0, related_alert_count=0, transaction_volume_band=0)
    medium = ComplexityInputs(alert_id="a", entity_count=4, jurisdiction_count=1, missing_kyc_count=1,
                               source_system_count=1, related_alert_count=0, transaction_volume_band=0)
    high = ComplexityInputs(alert_id="a", entity_count=4, jurisdiction_count=1, missing_kyc_count=1,
                             source_system_count=1, related_alert_count=1, transaction_volume_band=0)
    very_high = ComplexityInputs(alert_id="a", entity_count=10, jurisdiction_count=5, missing_kyc_count=5,
                                  source_system_count=5, related_alert_count=5, transaction_volume_band=3)

    assert score_alert(base_inputs, low, policy, now=NOW).complexity_band == ComplexityBand.LOW
    assert score_alert(base_inputs, medium, policy, now=NOW).complexity_band == ComplexityBand.MEDIUM
    assert score_alert(base_inputs, high, policy, now=NOW).complexity_band == ComplexityBand.HIGH
    assert score_alert(base_inputs, very_high, policy, now=NOW).complexity_band == ComplexityBand.VERY_HIGH


# -- caveats / unresolved fields -------------------------------------------------


def test_unresolved_fields_become_caveats(policy, base_inputs, base_complexity):
    inputs = base_inputs.model_copy(update={"unresolved_fields": ["TRANSACTION_BASELINES not found"]})
    result = score_alert(inputs, base_complexity, policy, now=NOW)
    assert any("TRANSACTION_BASELINES not found" in c for c in result.caveats)


def test_policy_version_stamped_on_result_and_every_factor(policy, base_inputs, base_complexity):
    result = score_alert(base_inputs, base_complexity, policy, now=NOW)
    assert result.policy_version == policy.policy_version
    assert all(f.policy_version == policy.policy_version for f in result.factors)


def test_calculated_at_uses_provided_now(policy, base_inputs, base_complexity):
    custom_now = NOW + timedelta(days=1)
    result = score_alert(base_inputs, base_complexity, policy, now=custom_now)
    assert result.calculated_at == custom_now
