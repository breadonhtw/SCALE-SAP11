from functools import lru_cache

from trustsphere.prediction.base import PredictionResult, SLAPredictor
from trustsphere.prediction.heuristic import HeuristicSLAPredictor
from trustsphere.prediction.local_ml import LocalMLSLAPredictor

__all__ = ["PredictionResult", "SLAPredictor", "get_predictor"]


@lru_cache
def get_predictor() -> SLAPredictor:
    from trustsphere.config import get_settings

    backend = get_settings().prediction_backend
    if backend == "heuristic":
        return HeuristicSLAPredictor()
    return LocalMLSLAPredictor()  # transparently falls back to heuristic if untrained
