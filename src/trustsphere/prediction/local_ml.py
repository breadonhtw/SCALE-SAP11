"""Local open-source regression model behind SLAPredictor (CLAUDE.md §0, §10).

PAL/APL is confirmed unavailable on the tenant (docs/capability-matrix.md).
This is the disclosed fallback: scikit-learn, trained locally, model
artifact + metadata persisted to disk and referenced by version in every
prediction. If no trained artifact exists yet (fresh checkout, or the
scripts/train_sla_model.py step hasn't been run against enough closed
alerts), this transparently delegates to `HeuristicSLAPredictor` rather than
pretending to have a model — `model_name` always tells the truth.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from trustsphere.config.settings import REPO_ROOT
from trustsphere.domain.alerts import AlertFactorInputs, ComplexityInputs
from trustsphere.prediction.base import PredictionResult, SLAPredictor
from trustsphere.prediction.features import build_feature_vector
from trustsphere.prediction.heuristic import HeuristicSLAPredictor

MODEL_PATH = REPO_ROOT / "data" / "models" / "sla_model.joblib"
METADATA_PATH = REPO_ROOT / "data" / "models" / "sla_model_metadata.json"
SMALL_SAMPLE_THRESHOLD = 200


class LocalMLSLAPredictor(SLAPredictor):
    """Loads a persisted scikit-learn regressor if one has been trained
    (see scripts/train_sla_model.py); otherwise falls back to the heuristic.
    """

    def __init__(self):
        self._model = None
        self._metadata: dict | None = None
        self._fallback = HeuristicSLAPredictor()
        self._try_load()

    def _try_load(self) -> None:
        if not MODEL_PATH.exists() or not METADATA_PATH.exists():
            return
        try:
            import joblib

            self._model = joblib.load(MODEL_PATH)
            self._metadata = json.loads(METADATA_PATH.read_text())
        except Exception:
            self._model = None
            self._metadata = None

    def predict(
        self,
        alert_id: str,
        inputs: AlertFactorInputs,
        complexity_inputs: ComplexityInputs,
        as_of: datetime,
    ) -> PredictionResult:
        if self._model is None or self._metadata is None:
            result = self._fallback.predict(alert_id, inputs, complexity_inputs, as_of)
            result.extra["fallback_reason"] = "no trained model artifact found — run scripts/train_sla_model.py"
            return result

        vec = [build_feature_vector(inputs, complexity_inputs)]
        expected_hours = float(self._model.predict(vec)[0])
        expected_hours = max(expected_hours, 1.0)

        n_samples = self._metadata.get("n_samples", 0)
        small_sample = n_samples < SMALL_SAMPLE_THRESHOLD

        sla_risk_score = None
        margin_hours = None
        if inputs.hours_remaining_to_sla is not None:
            margin_hours = inputs.hours_remaining_to_sla - expected_hours
            sla_risk_score = 1.0 / (1.0 + pow(2.71828, margin_hours / 200.0))

        return PredictionResult(
            prediction_value=round(expected_hours, 1),
            model_name=self._metadata.get("model_name", "local_sklearn_gbr"),
            model_version=self._metadata.get("model_version", "unknown"),
            feature_snapshot_id=f"local_ml:{alert_id}:{as_of.isoformat()}",
            extra={
                "sla_risk_score": round(sla_risk_score, 4) if sla_risk_score is not None else None,
                "margin_hours": round(margin_hours, 1) if margin_hours is not None else None,
                "n_training_samples": n_samples,
                "small_sample_warning": small_sample,
                "trained_at": self._metadata.get("trained_at"),
                "evaluation": self._metadata.get("evaluation", {}),
                "note": "Local open-source regression (PAL/APL unavailable on tenant — "
                "see docs/capability-matrix.md). Advisory demonstration only.",
            },
        )
