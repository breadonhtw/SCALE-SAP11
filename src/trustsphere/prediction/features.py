"""Shared featurisation for the SLA predictor — used identically by training
(scripts/train_sla_model.py) and inference (prediction/local_ml.py,
prediction/heuristic.py) so there is no train/serve skew. Matches
config/feature_schema.yaml.
"""

from __future__ import annotations

import math

from trustsphere.domain.alerts import AlertFactorInputs, ComplexityInputs

FEATURE_ORDER = [
    "alert_priority_ordinal",
    "composite_risk_score",
    "country_risk_score",
    "is_cross_border",
    "amount_usd_log",
    "entity_count",
    "jurisdiction_count",
    "missing_kyc_count",
    "related_alert_count",
    "transaction_volume_band",
]

_PRIORITY_ORDINAL = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}


def build_feature_vector(inputs: AlertFactorInputs, complexity: ComplexityInputs) -> list[float]:
    amount = float(inputs.amount_usd) if inputs.amount_usd is not None else 0.0
    return [
        float(_PRIORITY_ORDINAL.get(inputs.alert_priority or "", 1)),
        float(inputs.composite_risk_score) if inputs.composite_risk_score is not None else 50.0,
        float(inputs.country_risk_score) if inputs.country_risk_score is not None else 40.0,
        1.0 if inputs.is_cross_border else 0.0,
        math.log1p(max(amount, 0.0)),
        float(complexity.entity_count),
        float(complexity.jurisdiction_count),
        float(complexity.missing_kyc_count),
        float(complexity.related_alert_count),
        float(complexity.transaction_volume_band),
    ]
