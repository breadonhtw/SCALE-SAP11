from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header

from trustsphere.api.deps import get_repo, get_scoring_policy
from trustsphere.api.errors import AppError, NotFoundError
from trustsphere.api.schemas import PredictSlaRequest, ScoreAlertRequest
from trustsphere.persistence.base import Repository
from trustsphere.prediction import get_predictor
from trustsphere.scoring.engine import score_alert
from trustsphere.scoring.policy import ScoringPolicy
from trustsphere.services.idempotency import check_idempotency, compute_request_hash

router = APIRouter(tags=["alerts"])


def _parse_as_of(as_of: str | None) -> datetime:
    if not as_of:
        return datetime.now(timezone.utc)
    # Accept a trailing 'Z' (the common JS/curl ISO-8601 UTC suffix) on any
    # Python 3.11+ runtime that predates the fromisoformat 'Z' support added
    # upstream — a client should never get a 500 for a spec-valid timestamp.
    normalised = as_of[:-1] + "+00:00" if as_of.endswith("Z") else as_of
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError:
        raise AppError("INVALID_TIMESTAMP", f"as_of must be an ISO-8601 timestamp, got {as_of!r}", status_code=422)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("/alerts")
def list_alerts(
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
    repo: Repository = Depends(get_repo),
):
    alerts = repo.list_alerts(status=status, limit=limit, offset=offset)
    return {"backend": repo.backend_label(), "count": len(alerts), "alerts": [a.model_dump() for a in alerts]}


@router.get("/alerts/queue")
def ranked_queue(limit: int = 200, offset: int = 0,
                 repo: Repository = Depends(get_repo)):
    """Deterministic queue order: hard overrides -> tier -> SLA remaining ->
    urgency score -> complexity as tie-break (CLAUDE.md §9 "Queue policy").
    Requires alerts to have been scored first via POST /alerts/{id}/score.
    """
    rows = repo.list_scored_alerts_ordered(limit=limit, offset=offset)
    return {"backend": repo.backend_label(), "count": len(rows),
            "total": repo.count_scored_open_alerts(),
            "limit": limit, "offset": offset, "queue": rows}


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str, repo: Repository = Depends(get_repo)):
    alert = repo.get_alert(alert_id)
    if alert is None:
        raise NotFoundError(f"alert_id {alert_id!r} not found")
    score = repo.get_latest_score(alert_id)
    prediction = repo.get_latest_predictive_score(alert_id)
    return {
        "backend": repo.backend_label(),
        "alert": alert.model_dump(),
        "priority_score": score.model_dump() if score else None,
        "predictive_sla": prediction,
    }


@router.post("/alerts/{alert_id}/score")
def score_alert_endpoint(
    alert_id: str,
    body: ScoreAlertRequest = ScoreAlertRequest(),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    repo: Repository = Depends(get_repo),
    policy: ScoringPolicy = Depends(get_scoring_policy),
):
    endpoint = "POST /alerts/{id}/score"
    req_hash = compute_request_hash({"alert_id": alert_id, **body.model_dump()})
    if idempotency_key:
        cached = check_idempotency(repo, idempotency_key, endpoint, req_hash)
        if cached is not None:
            return cached

    if repo.get_alert(alert_id) is None:
        raise NotFoundError(f"alert_id {alert_id!r} not found")

    as_of = _parse_as_of(body.as_of)
    inputs = repo.get_alert_factor_inputs(alert_id, as_of)
    complexity_inputs = repo.get_complexity_inputs(alert_id)
    result = score_alert(inputs, complexity_inputs, policy, now=as_of)
    repo.save_score(result)

    response = {"backend": repo.backend_label(), "score": result.model_dump()}
    if idempotency_key:
        repo.store_idempotent_response(idempotency_key, endpoint, req_hash, response)
    return response


@router.post("/alerts/score-all")
def score_all_alerts(repo: Repository = Depends(get_repo), policy: ScoringPolicy = Depends(get_scoring_policy)):
    """Batch scorer — scores every alert currently in RISK_ALERTS and
    persists (CLAUDE.md A2 "batch scorer persisting to HANA").
    """
    as_of = datetime.now(timezone.utc)
    scored = []
    offset = 0
    while True:
        batch = repo.list_alerts(limit=500, offset=offset)
        if not batch:
            break
        for a in batch:
            inputs = repo.get_alert_factor_inputs(a.alert_id, as_of)
            complexity_inputs = repo.get_complexity_inputs(a.alert_id)
            result = score_alert(inputs, complexity_inputs, policy, now=as_of)
            repo.save_score(result)
            scored.append(a.alert_id)
        offset += 500
    return {"backend": repo.backend_label(), "scored_count": len(scored)}


@router.post("/alerts/{alert_id}/predict-sla")
def predict_sla_endpoint(
    alert_id: str,
    body: PredictSlaRequest = PredictSlaRequest(),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    repo: Repository = Depends(get_repo),
):
    endpoint = "POST /alerts/{id}/predict-sla"
    req_hash = compute_request_hash({"alert_id": alert_id, **body.model_dump()})
    if idempotency_key:
        cached = check_idempotency(repo, idempotency_key, endpoint, req_hash)
        if cached is not None:
            return cached

    if repo.get_alert(alert_id) is None:
        raise NotFoundError(f"alert_id {alert_id!r} not found")

    as_of = _parse_as_of(body.as_of)
    predictor = get_predictor()
    inputs = repo.get_alert_factor_inputs(alert_id, as_of)
    complexity_inputs = repo.get_complexity_inputs(alert_id)
    result = predictor.predict(alert_id, inputs, complexity_inputs, as_of)
    repo.save_predictive_score(
        alert_id=alert_id,
        prediction_type=result.prediction_type,
        prediction_value=result.prediction_value,
        model_name=result.model_name,
        model_version=result.model_version,
        feature_snapshot_id=result.feature_snapshot_id,
        scored_at=as_of,
        extra=result.extra,
    )
    response = {"backend": repo.backend_label(), "prediction": result.model_dump()}
    if idempotency_key:
        repo.store_idempotent_response(idempotency_key, endpoint, req_hash, response)
    return response
