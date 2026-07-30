"""SLAPredictor interface (CLAUDE.md §10, §18).

Every implementation returns the same `PredictionResult` shape whether it's
the trained local model or the heuristic fallback — callers (the API layer)
never branch on which one ran; `model_name` tells the honest story.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel

from trustsphere.domain.alerts import AlertFactorInputs, ComplexityInputs


class PredictionResult(BaseModel):
    prediction_type: str = "expected_resolution_hours"
    prediction_value: float
    model_name: str
    model_version: str
    feature_snapshot_id: str
    advisory_only: bool = True
    label: str = "Advisory — pilot/shadow mode"
    extra: dict = {}


class SLAPredictor(ABC):
    @abstractmethod
    def predict(
        self,
        alert_id: str,
        inputs: AlertFactorInputs,
        complexity_inputs: ComplexityInputs,
        as_of: datetime,
    ) -> PredictionResult:
        ...
