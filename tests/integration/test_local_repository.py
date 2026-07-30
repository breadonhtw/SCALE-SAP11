"""Integration tests against `LocalSQLiteRepository` — real SQL, throwaway
DB per test (CLAUDE.md §25 "Persistence round trips").
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trustsphere.domain.decisions import (
    ActorType,
    AuditEvent,
    AuditEventType,
    Decision,
    DecisionType,
    WorkflowInstance,
    WorkflowStatus,
)
from trustsphere.persistence.local import LocalSQLiteRepository
from trustsphere.scoring.engine import score_alert

from ..fixtures.minimal_seed import NOW, seed_minimal


@pytest.fixture
def repo(tmp_path):
    r = LocalSQLiteRepository(str(tmp_path / "test.db"))
    seed_minimal(r)
    return r


def test_backend_label_never_claims_hana(repo):
    assert repo.backend_label() == "local_sqlite_fallback"


def test_health_check_reports_seeded_alert_count(repo):
    health = repo.health_check()
    assert health["ok"] is True
    assert health["alert_count"] == 3  # ALERT-T1, ALERT-T2, ALERT-T3


# -- queue ordering (A2 "queue ordering") ---------------------------------------


def test_queue_excludes_closed_alerts(repo, policy):
    for alert_id in ["ALERT-T1", "ALERT-T2"]:
        inputs = repo.get_alert_factor_inputs(alert_id, NOW)
        complexity = repo.get_complexity_inputs(alert_id)
        repo.save_score(score_alert(inputs, complexity, policy, now=NOW))

    queue = repo.list_scored_alerts_ordered()
    alert_ids = [row["ALERT_ID"] for row in queue]
    assert "ALERT-T3" not in alert_ids  # closed, never scored, and must not leak in even if scored


def test_queue_orders_hard_override_before_higher_raw_score(repo, policy):
    """ALERT-T2 has fewer than 4 hours to SLA -> IMMINENT_SLA_BREACH hard
    override -> CRITICAL. ALERT-T1 has no override. Queue policy (CLAUDE.md
    §9): hard overrides rank first regardless of raw urgency_score.
    """
    for alert_id in ["ALERT-T1", "ALERT-T2"]:
        inputs = repo.get_alert_factor_inputs(alert_id, NOW)
        complexity = repo.get_complexity_inputs(alert_id)
        repo.save_score(score_alert(inputs, complexity, policy, now=NOW))

    queue = repo.list_scored_alerts_ordered()
    assert queue[0]["ALERT_ID"] == "ALERT-T2"
    assert queue[0]["HARD_OVERRIDE_CODE"] == "IMMINENT_SLA_BREACH"


def test_save_score_is_idempotent_replace_not_duplicate(repo, policy):
    inputs = repo.get_alert_factor_inputs("ALERT-T1", NOW)
    complexity = repo.get_complexity_inputs("ALERT-T1")
    repo.save_score(score_alert(inputs, complexity, policy, now=NOW))
    repo.save_score(score_alert(inputs, complexity, policy, now=NOW))
    rows = repo.conn.execute("SELECT COUNT(*) FROM PRIORITY_SCORES WHERE ALERT_ID='ALERT-T1'").fetchone()
    assert rows[0] == 1
    factor_rows = repo.conn.execute("SELECT COUNT(*) FROM ALERT_FACTORS WHERE ALERT_ID='ALERT-T1'").fetchone()
    assert factor_rows[0] == 5  # not doubled to 10


# -- decisions / workflow / audit (A5) -------------------------------------------


def test_case_creation_is_idempotent_per_alert(repo):
    case_id_1 = repo.get_or_create_case("ALERT-T1", "financial-crime-ops", "APJ")
    case_id_2 = repo.get_or_create_case("ALERT-T1", "financial-crime-ops", "APJ")
    assert case_id_1 == case_id_2


def test_decision_round_trip(repo):
    case_id = repo.get_or_create_case("ALERT-T1", "financial-crime-ops", "APJ")
    decision = Decision(
        decision_id="DEC-1", case_id=case_id, decision_type=DecisionType.APPROVE_FOR_ESCALATION,
        rationale="test", decided_by="analyst.jane", attested=True,
        decided_at=datetime.now(timezone.utc),
    )
    repo.save_decision(decision)
    stored = repo.list_decisions(case_id)
    assert len(stored) == 1
    assert stored[0].decided_by == "analyst.jane"
    assert stored[0].attested is True


def test_workflow_transition_updates_external_instance_id_and_is_fallback(repo):
    """Regression test: `save_workflow_instance`'s UPDATE branch must persist
    EXTERNAL_INSTANCE_ID and IS_FALLBACK, not just STATUS/COMPLETED_AT — the
    whole point of the transition is to record the live SBPA callback
    details and flip is_fallback to False.
    """
    case_id = repo.get_or_create_case("ALERT-T1", "financial-crime-ops", "APJ")
    started = WorkflowInstance(
        workflow_id="WF-1", case_id=case_id, external_instance_id=None,
        status=WorkflowStatus.PENDING, started_at=datetime.now(timezone.utc),
        completed_at=None, is_fallback=True,
    )
    repo.save_workflow_instance(started)

    transitioned = WorkflowInstance(
        workflow_id="WF-1", case_id=case_id, external_instance_id="SBPA-INST-999",
        status=WorkflowStatus.APPROVED, started_at=started.started_at,
        completed_at=datetime.now(timezone.utc), is_fallback=False,
    )
    repo.save_workflow_instance(transitioned)

    fetched = repo.get_latest_workflow_instance(case_id)
    assert fetched.status == WorkflowStatus.APPROVED
    assert fetched.external_instance_id == "SBPA-INST-999"
    assert fetched.is_fallback is False


def test_audit_events_are_append_only_and_ordered(repo):
    case_id = repo.get_or_create_case("ALERT-T1", "financial-crime-ops", "APJ")
    for i in range(3):
        repo.append_audit_event(AuditEvent(
            event_id=f"EVT-{i}", case_id=case_id, event_type=AuditEventType.CASE_FILE_ASSEMBLED,
            actor_type=ActorType.SYSTEM, actor_id="tester", object_type="CASE_FILE", object_id="cf-1",
            details={"i": i}, occurred_at=datetime.now(timezone.utc), correlation_id=f"corr-{i}",
        ))
    events = repo.list_audit_events(case_id)
    assert [e.event_id for e in events] == ["EVT-0", "EVT-1", "EVT-2"]
    # Repository interface exposes no update/delete for audit events at all.
    assert not hasattr(repo, "update_audit_event")
    assert not hasattr(repo, "delete_audit_event")


# -- idempotency ------------------------------------------------------------------


def test_idempotency_key_replay_returns_stored_response(repo):
    key, endpoint, req_hash = "idem-1", "POST /x", "hash-1"
    assert repo.check_and_store_idempotency_key(key, endpoint, req_hash) is None
    repo.store_idempotent_response(key, endpoint, req_hash, {"ok": True})
    replayed = repo.check_and_store_idempotency_key(key, endpoint, req_hash)
    assert replayed == {"ok": True}


def test_idempotency_key_reused_with_different_body_raises(repo):
    key, endpoint = "idem-2", "POST /x"
    repo.store_idempotent_response(key, endpoint, "hash-a", {"ok": True})
    with pytest.raises(ValueError):
        repo.check_and_store_idempotency_key(key, endpoint, "hash-b")
