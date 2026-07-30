"""B3 review-flow integration test: draft edit -> workflow -> attested
decision -> audit ordering. Exercises the same endpoints the Review & Decide
page calls (CLAUDE.md §14 human accountability, §7 append-only audit).
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


def test_review_flow_edit_workflow_attested_decision_audit(client):
    # case + agent draft
    case_id = client.post("/alerts/ALERT-T1/cases").json()["case"]["CASE_ID"]
    agent_draft = client.post(f"/cases/{case_id}/drafts", json={
        "content": "Agent narrative. [cit-1]", "generation_id": "GEN-test",
        "prompt_version": "narrative-1.1", "model_version": "gpt-4.1-mini",
        "created_by_type": "agent", "verification_status": "unverified",
    }).json()["draft"]
    assert agent_draft["DRAFT_VERSION"] == 1

    # investigator edit -> v2, created_by human
    human_draft = client.post(f"/cases/{case_id}/drafts", json={
        "content": "Investigator-revised narrative. [cit-1]",
        "generation_id": "human-edit-1", "prompt_version": "n/a",
        "model_version": "n/a", "created_by_type": "human",
        "verification_status": "unverified",
    }).json()["draft"]
    assert human_draft["DRAFT_VERSION"] == 2
    assert human_draft["CREATED_BY_TYPE"] == "human"
    latest = client.get(f"/cases/{case_id}/drafts/latest").json()["draft"]
    assert latest["DRAFT_ID"] == human_draft["DRAFT_ID"]

    # review workflow starts as honest local fallback
    wf = client.post(f"/cases/{case_id}/review-workflows", json={
        "draft_id": human_draft["DRAFT_ID"], "senior_review": False,
    }).json()["workflow"]
    assert wf["is_fallback"] is True
    assert wf["status"] == "PENDING"

    # unattested decision is refused, not recorded
    refused = client.post(f"/cases/{case_id}/decisions", json={
        "decision_type": "approve_for_escalation", "rationale": "evidence solid",
        "decided_by": "investigator.demo", "attested": False,
    })
    assert refused.status_code == 422
    assert refused.json()["error_code"] == "ATTESTATION_REQUIRED"
    assert client.get(f"/cases/{case_id}/decisions").json()["count"] == 0

    # attested decision recorded; workflow transitioned to APPROVED
    decision = client.post(f"/cases/{case_id}/decisions", json={
        "decision_type": "approve_for_escalation", "rationale": "evidence solid",
        "decided_by": "investigator.demo", "attested": True,
    })
    assert decision.status_code == 200
    wf2 = client.patch(f"/cases/{case_id}/review-workflows",
                       json={"status": "APPROVED"}).json()["workflow"]
    assert wf2["status"] == "APPROVED"
    assert wf2["completed_at"] is not None
    assert wf2["is_fallback"] is True  # no external_instance_id supplied

    # audit trail contains the full ordered story
    events = [e["event_type"] for e in
              client.get(f"/cases/{case_id}/audit-events").json()["audit_events"]]
    for expected in ("DRAFT_CREATED", "WORKFLOW_STARTED",
                     "DECISION_RECORDED", "WORKFLOW_TRANSITIONED"):
        assert expected in events
    assert events.count("DRAFT_CREATED") == 2
    assert events.index("WORKFLOW_STARTED") < events.index("DECISION_RECORDED") \
        < events.index("WORKFLOW_TRANSITIONED")
