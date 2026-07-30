"""End-to-end API tests via FastAPI's TestClient — same routers the demo
hits, backed by a throwaway `LocalSQLiteRepository` per test through
`app.dependency_overrides` (never the real data/local_trustsphere.db).

Covers the full A1-A5 request path: score -> assemble -> draft -> decision
-> review-workflow -> audit trail, plus the human-accountability guard on
POST /cases/{id}/decisions (CLAUDE.md §14).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from trustsphere.api.app import app
from trustsphere.api.deps import get_repo, get_scoring_policy
from trustsphere.persistence.local import LocalSQLiteRepository
from trustsphere.scoring.policy import load_policy

from ..fixtures.minimal_seed import seed_minimal


@pytest.fixture
def client(tmp_path):
    repo = LocalSQLiteRepository(str(tmp_path / "test.db"))
    seed_minimal(repo)

    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_scoring_policy] = lambda: load_policy()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["backend"] == "local_sqlite_fallback"


def test_score_then_queue_reflects_hard_override(client):
    resp = client.post("/alerts/ALERT-T2/score")
    assert resp.status_code == 200
    assert resp.json()["score"]["hard_override"]["code"] == "IMMINENT_SLA_BREACH"

    resp = client.get("/alerts/queue")
    assert resp.status_code == 200
    ids = [row["ALERT_ID"] for row in resp.json()["queue"]]
    assert ids == ["ALERT-T2"]  # only alert scored so far, closed alert excluded


def test_score_unknown_alert_returns_404(client):
    resp = client.post("/alerts/ALERT-DOES-NOT-EXIST/score")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "NOT_FOUND"


def test_full_case_flow_scores_assembles_drafts_decides_and_audits(client):
    # 1. score
    assert client.post("/alerts/ALERT-T1/score").status_code == 200
    # 2. predictive SLA (advisory)
    pred = client.post("/alerts/ALERT-T1/predict-sla")
    assert pred.status_code == 200
    assert pred.json()["prediction"]["advisory_only"] is True

    # 3. create case
    case_resp = client.post("/alerts/ALERT-T1/cases", json={})
    assert case_resp.status_code == 200
    case_id = case_resp.json()["case"]["CASE_ID"]

    # 4. assemble CaseFile — exact facts + citations, no invented values
    assemble_resp = client.post(f"/cases/{case_id}/assemble")
    assert assemble_resp.status_code == 200
    case_file = assemble_resp.json()["case_file"]
    assert case_file["source_provenance"]  # every claim traces to a citation
    assert case_file["alert_details"]["alert_id"] == "ALERT-T1"

    # 5. draft (would normally come from Track B's generation endpoint)
    draft_resp = client.post(f"/cases/{case_id}/drafts", json={
        "content": "Draft narrative.", "created_by_type": "agent", "generation_id": "gen-1",
    })
    assert draft_resp.status_code == 200

    # 6. decision without attestation is refused
    unattested = client.post(f"/cases/{case_id}/decisions", json={
        "decision_type": "approve_for_escalation", "rationale": "r", "decided_by": "analyst.jane",
        "attested": False,
    })
    assert unattested.status_code == 422
    assert unattested.json()["error_code"] == "ATTESTATION_REQUIRED"

    # 7. attested decision succeeds
    decided = client.post(f"/cases/{case_id}/decisions", json={
        "decision_type": "approve_for_escalation", "rationale": "r", "decided_by": "analyst.jane",
        "attested": True,
    })
    assert decided.status_code == 200

    # 8. start + transition the human-review workflow (B5's SBPA trigger/callback target)
    started = client.post(f"/cases/{case_id}/review-workflows", json={"senior_review": False})
    assert started.status_code == 200
    assert started.json()["workflow"]["is_fallback"] is True  # no SBPA configured in this test env

    transitioned = client.patch(f"/cases/{case_id}/review-workflows", json={
        "status": "APPROVED", "external_instance_id": "SBPA-INST-1",
    })
    assert transitioned.status_code == 200
    assert transitioned.json()["workflow"]["external_instance_id"] == "SBPA-INST-1"
    assert transitioned.json()["workflow"]["is_fallback"] is False

    # 9. audit trail has every step, oldest first
    audit_resp = client.get(f"/cases/{case_id}/audit-events")
    assert audit_resp.status_code == 200
    event_types = [e["event_type"] for e in audit_resp.json()["audit_events"]]
    assert event_types == [
        "CASE_FILE_ASSEMBLED", "DRAFT_CREATED", "DECISION_RECORDED",
        "WORKFLOW_STARTED", "WORKFLOW_TRANSITIONED",
    ]

    # 10. GET /cases/{id} is the same shared state Streamlit and the agent both read
    final = client.get(f"/cases/{case_id}")
    assert final.status_code == 200
    assert len(final.json()["decisions"]) == 1
    assert final.json()["workflow"]["status"] == "APPROVED"


def test_decisions_endpoint_rejects_unknown_decision_type(client):
    case_resp = client.post("/alerts/ALERT-T1/cases", json={})
    case_id = case_resp.json()["case"]["CASE_ID"]
    resp = client.post(f"/cases/{case_id}/decisions", json={
        "decision_type": "auto_file_sar", "rationale": "r", "decided_by": "x", "attested": True,
    })
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_DECISION_TYPE"


def test_idempotency_key_on_score_returns_identical_response(client):
    headers = {"Idempotency-Key": "test-key-1"}
    first = client.post("/alerts/ALERT-T1/score", headers=headers)
    second = client.post("/alerts/ALERT-T1/score", headers=headers)
    assert first.json() == second.json()


def test_idempotency_key_reused_with_different_body_is_a_clean_conflict(client):
    headers = {"Idempotency-Key": "test-key-2"}
    client.post("/alerts/ALERT-T1/score", headers=headers, json={"as_of": "2026-01-01T00:00:00Z"})
    resp = client.post("/alerts/ALERT-T1/score", headers=headers, json={"as_of": "2026-06-01T00:00:00Z"})
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "CONFLICT"
