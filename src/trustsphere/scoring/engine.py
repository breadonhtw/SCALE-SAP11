"""Pure deterministic scoring engine (CLAUDE.md §9).

No I/O, no vendor SDKs, no randomness. Takes `AlertFactorInputs` +
`ComplexityInputs` (already resolved by the persistence/retrieval layer) and
a `ScoringPolicy`, returns a `ScoreResult`. This is what unit tests exercise
directly — no DB, no HTTP.
"""

from __future__ import annotations

from datetime import datetime, timezone

from trustsphere.domain.alerts import (
    AlertFactorInputs,
    ComplexityBand,
    ComplexityInputs,
    FactorResult,
    HardOverrideResult,
    ScoreResult,
    UrgencyTier,
)
from trustsphere.scoring.policy import ScoringPolicy


def _piecewise_hours_remaining(value: float | None, cfg: dict) -> tuple[float, str]:
    if value is None:
        return cfg["default"], cfg["missing_reason"]
    for bp in cfg["breakpoints_hours_remaining"]:
        if "at_or_below" in bp and value <= bp["at_or_below"]:
            return float(bp["score"]), f"hours_remaining={value:.1f} <= {bp['at_or_below']}"
    for bp in cfg["breakpoints_hours_remaining"]:
        if "above" in bp:
            return float(bp["score"]), f"hours_remaining={value:.1f} > {bp['above']}"
    return cfg["default"], cfg["missing_reason"]


def _piecewise_ratio(value: float | None, cfg: dict) -> tuple[float, str]:
    if value is None:
        return cfg["default"], cfg["missing_reason"]
    for bp in cfg["ratio_breakpoints"]:
        if "at_or_below" in bp and value <= bp["at_or_below"]:
            return float(bp["score"]), f"amount/baseline_ratio={value:.2f} <= {bp['at_or_below']}"
    for bp in cfg["ratio_breakpoints"]:
        if "above" in bp:
            return float(bp["score"]), f"amount/baseline_ratio={value:.2f} > {bp['above']}"
    return cfg["default"], cfg["missing_reason"]


def _factor_typology_severity(inputs: AlertFactorInputs, policy: ScoringPolicy) -> FactorResult:
    cfg = policy.normalisation["typology_severity"]
    priority = inputs.alert_priority
    if priority and priority in cfg["by_alert_priority"]:
        value = float(cfg["by_alert_priority"][priority])
        reason = f"ALERT_PRIORITY={priority}"
    else:
        value, reason = cfg["default"], cfg["missing_reason"]
    weight = policy.factors["typology_severity"].weight
    return FactorResult(
        factor_code="typology_severity", raw_value=priority, normalised_value=value,
        weight=weight, weighted_points=round(value * weight, 4), reason_code=reason,
        policy_version=policy.policy_version,
    )


def _factor_entity_risk(inputs: AlertFactorInputs, policy: ScoringPolicy) -> FactorResult:
    cfg = policy.normalisation["entity_risk"]
    if inputs.composite_risk_score is None:
        value, reason = cfg["default"], cfg["missing_reason"]
    else:
        base = float(inputs.composite_risk_score)
        penalty = cfg["kyc_status_penalty"].get(inputs.kyc_effective_status or "", 0)
        value = min(100.0, base + penalty)
        reason = f"COMPOSITE_RISK_SCORE={base:.1f} + KYC[{inputs.kyc_effective_status}]_penalty={penalty}"
    weight = policy.factors["entity_risk"].weight
    return FactorResult(
        factor_code="entity_risk", raw_value=str(inputs.composite_risk_score), normalised_value=value,
        weight=weight, weighted_points=round(value * weight, 4), reason_code=reason,
        policy_version=policy.policy_version,
    )


def _factor_jurisdiction_exposure(inputs: AlertFactorInputs, policy: ScoringPolicy) -> FactorResult:
    cfg = policy.normalisation["jurisdiction_exposure"]
    if inputs.country_risk_score is None and inputs.risk_tier is None:
        value, reason = cfg["default"], cfg["missing_reason"]
    else:
        base = float(inputs.country_risk_score) if inputs.country_risk_score is not None else 0.0
        floor = cfg["risk_tier_floor"].get(inputs.risk_tier or "", 0)
        value = max(base, floor)
        if inputs.is_cross_border:
            value += cfg["cross_border_bonus"]
        value = min(100.0, value)
        reason = f"COUNTRY_RISK_SCORE={base:.1f}, RISK_TIER={inputs.risk_tier} floor={floor}, cross_border={inputs.is_cross_border}"
    weight = policy.factors["jurisdiction_exposure"].weight
    return FactorResult(
        factor_code="jurisdiction_exposure", raw_value=inputs.risk_tier, normalised_value=value,
        weight=weight, weighted_points=round(value * weight, 4), reason_code=reason,
        policy_version=policy.policy_version,
    )


def _factor_sla_age_proximity(inputs: AlertFactorInputs, policy: ScoringPolicy) -> FactorResult:
    cfg = policy.normalisation["sla_age_proximity"]
    value, reason = _piecewise_hours_remaining(inputs.hours_remaining_to_sla, cfg)
    weight = policy.factors["sla_age_proximity"].weight
    return FactorResult(
        factor_code="sla_age_proximity",
        raw_value=str(inputs.hours_remaining_to_sla) if inputs.hours_remaining_to_sla is not None else None,
        normalised_value=value, weight=weight, weighted_points=round(value * weight, 4),
        reason_code=reason, policy_version=policy.policy_version,
    )


def _factor_transaction_materiality(inputs: AlertFactorInputs, policy: ScoringPolicy) -> FactorResult:
    cfg = policy.normalisation["transaction_materiality"]
    ratio = None
    if inputs.amount_usd is not None and inputs.baseline_avg_amount_usd not in (None, 0):
        ratio = float(inputs.amount_usd) / float(inputs.baseline_avg_amount_usd)
    value, reason = _piecewise_ratio(ratio, cfg)
    weight = policy.factors["transaction_materiality"].weight
    return FactorResult(
        factor_code="transaction_materiality", raw_value=str(inputs.amount_usd), normalised_value=value,
        weight=weight, weighted_points=round(value * weight, 4), reason_code=reason,
        policy_version=policy.policy_version,
    )


_FACTOR_FUNCS = [
    _factor_typology_severity,
    _factor_entity_risk,
    _factor_jurisdiction_exposure,
    _factor_sla_age_proximity,
    _factor_transaction_materiality,
]


def _evaluate_hard_overrides(
    inputs: AlertFactorInputs, policy: ScoringPolicy
) -> tuple[HardOverrideResult | None, list[str]]:
    caveats: list[str] = []
    for ov in policy.hard_overrides:
        value = getattr(inputs, ov.field, None)
        if value is None:
            continue  # unresolved field — never guessed, never triggers
        triggered = False
        if ov.equals is not None and value == ov.equals:
            triggered = True
        if ov.at_or_below is not None and isinstance(value, (int, float)) and value <= ov.at_or_below:
            triggered = True
        if ov.at_or_above is not None and isinstance(value, (int, float)) and value >= ov.at_or_above:
            triggered = True
        if triggered:
            return HardOverrideResult(
                code=ov.code, forced_tier=UrgencyTier(ov.forced_tier), reason=ov.reason
            ), caveats
    return None, caveats


def _complexity(inputs: ComplexityInputs, policy: ScoringPolicy) -> tuple[ComplexityBand, int]:
    w = policy.complexity.weights
    points = (
        inputs.entity_count * w.get("entity_count", 0)
        + inputs.jurisdiction_count * w.get("jurisdiction_count", 0)
        + inputs.missing_kyc_count * w.get("missing_kyc_count", 0)
        + inputs.source_system_count * w.get("source_system_count", 0)
        + inputs.related_alert_count * w.get("related_alert_count", 0)
        + inputs.transaction_volume_band * w.get("transaction_volume_band", 0)
    )
    for band_cfg in sorted(policy.complexity.bands, key=lambda b: b.max_points):
        if points <= band_cfg.max_points:
            return ComplexityBand(band_cfg.band), points
    return ComplexityBand(policy.complexity.bands[-1].band), points


def score_alert(
    inputs: AlertFactorInputs,
    complexity_inputs: ComplexityInputs,
    policy: ScoringPolicy,
    now: datetime | None = None,
) -> ScoreResult:
    now = now or datetime.now(timezone.utc)

    factors = [fn(inputs, policy) for fn in _FACTOR_FUNCS]
    urgency_score = round(sum(f.weighted_points for f in factors), 2)

    hard_override, caveats = _evaluate_hard_overrides(inputs, policy)
    urgency_tier = hard_override.forced_tier if hard_override else UrgencyTier(policy.tier_for_score(urgency_score))

    complexity_band, complexity_points = _complexity(complexity_inputs, policy)

    caveats = list(caveats) + [f"unresolved: {u}" for u in inputs.unresolved_fields]

    return ScoreResult(
        alert_id=inputs.alert_id,
        urgency_score=urgency_score,
        urgency_tier=urgency_tier,
        hard_override=hard_override,
        factors=factors,
        complexity_band=complexity_band,
        complexity_points=complexity_points,
        policy_version=policy.policy_version,
        calculated_at=now,
        caveats=caveats,
    )
