"""Transparent heuristic SLA-margin fallback (CLAUDE.md §18 — "Report actual
model used"). Always available, no training data required.

Formula: expected_resolution_hours scales a base handling time by priority,
complexity, and risk — calibrated loosely to the tenant's observed mean
closed-alert duration (~10,700h, docs/data-quality-report.md). This is a
transparent multiplier chain, not a fitted model — every factor is visible
in `extra.heuristic_factors`.
"""

from __future__ import annotations

from datetime import datetime

from trustsphere.domain.alerts import AlertFactorInputs, ComplexityInputs
from trustsphere.prediction.base import PredictionResult, SLAPredictor

BASE_HOURS = 400.0  # ~2.5 weeks, a deliberately conservative floor
PRIORITY_MULTIPLIER = {"CRITICAL": 1.8, "HIGH": 1.4, "MEDIUM": 1.0, "LOW": 0.7}


class HeuristicSLAPredictor(SLAPredictor):
    model_version = "heuristic-v1"

    def predict(
        self,
        alert_id: str,
        inputs: AlertFactorInputs,
        complexity_inputs: ComplexityInputs,
        as_of: datetime,
    ) -> PredictionResult:
        priority_mult = PRIORITY_MULTIPLIER.get(inputs.alert_priority or "", 1.0)
        risk_mult = 1.0 + (float(inputs.composite_risk_score) / 200.0 if inputs.composite_risk_score is not None else 0.25)
        complexity_mult = 1.0 + 0.08 * (
            complexity_inputs.entity_count
            + complexity_inputs.jurisdiction_count
            + complexity_inputs.missing_kyc_count
            + complexity_inputs.related_alert_count
        )
        expected_hours = BASE_HOURS * priority_mult * risk_mult * complexity_mult

        sla_risk_score = None
        margin_hours = None
        if inputs.hours_remaining_to_sla is not None:
            margin_hours = inputs.hours_remaining_to_sla - expected_hours
            # squash margin into a 0-1 "advisory risk" figure — negative
            # margin (predicted duration exceeds time remaining) -> high risk
            sla_risk_score = 1.0 / (1.0 + pow(2.71828, margin_hours / 200.0))

        return PredictionResult(
            prediction_value=round(expected_hours, 1),
            model_name="heuristic_sla_margin",
            model_version=self.model_version,
            feature_snapshot_id=f"heuristic:{alert_id}:{as_of.isoformat()}",
            extra={
                "sla_risk_score": round(sla_risk_score, 4) if sla_risk_score is not None else None,
                "margin_hours": round(margin_hours, 1) if margin_hours is not None else None,
                "heuristic_factors": {
                    "base_hours": BASE_HOURS,
                    "priority_multiplier": priority_mult,
                    "risk_multiplier": round(risk_mult, 3),
                    "complexity_multiplier": round(complexity_mult, 3),
                },
                "small_sample_warning": False,
                "note": "Transparent heuristic — not fitted to historical data.",
            },
        )
