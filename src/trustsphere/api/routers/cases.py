from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header

from trustsphere.api.deps import get_repo, get_scoring_policy
from trustsphere.api.errors import AppError, NotFoundError
from trustsphere.api.schemas import (
    AssembleCaseRequest,
    DecisionRequest,
    DraftRequest,
    ReviewWorkflowRequest,
    ReviewWorkflowTransitionRequest,
)
from trustsphere.config import get_settings
from trustsphere.domain.decisions import (
    ActorType,
    AuditEventType,
    Decision,
    DecisionType,
    WorkflowInstance,
    WorkflowStatus,
)
from trustsphere.persistence.base import Repository
from trustsphere.retrieval.hybrid import assemble_case_file
from trustsphere.scoring.policy import ScoringPolicy
from trustsphere.services.audit import record_event
from trustsphere.services.idempotency import check_idempotency, compute_request_hash

router = APIRouter(tags=["cases"])


@router.post("/alerts/{alert_id}/cases")
def create_or_get_case(
    alert_id: str,
    body: AssembleCaseRequest = AssembleCaseRequest(),
    repo: Repository = Depends(get_repo),
):
    if repo.get_alert(alert_id) is None:
        raise NotFoundError(f"alert_id {alert_id!r} not found")
    case_id = repo.get_or_create_case(alert_id, body.assigned_team, body.region)
    return {"backend": repo.backend_label(), "case": repo.get_case(case_id)}


@router.get("/cases/{case_id}")
def get_case(case_id: str, repo: Repository = Depends(get_repo)):
    case = repo.get_case(case_id)
    if case is None:
        raise NotFoundError(f"case_id {case_id!r} not found")
    case_file = repo.get_latest_case_file(case_id)
    draft = repo.get_latest_draft(case_id)
    workflow = repo.get_latest_workflow_instance(case_id)
    decisions = repo.list_decisions(case_id)
    return {
        "backend": repo.backend_label(),
        "case": case,
        "case_file": case_file.model_dump() if case_file else None,
        "latest_draft": draft,
        "workflow": workflow.model_dump() if workflow else None,
        "decisions": [d.model_dump() for d in decisions],
    }


@router.post("/cases/{case_id}/assemble")
def assemble_case(
    case_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    repo: Repository = Depends(get_repo),
    policy: ScoringPolicy = Depends(get_scoring_policy),
):
    endpoint = "POST /cases/{id}/assemble"
    req_hash = compute_request_hash({"case_id": case_id})
    if idempotency_key:
        cached = check_idempotency(repo, idempotency_key, endpoint, req_hash)
        if cached is not None:
            return cached

    case = repo.get_case(case_id)
    if case is None:
        raise NotFoundError(f"case_id {case_id!r} not found")

    settings = get_settings()
    case_file = assemble_case_file(
        repo, alert_id=case["ALERT_ID"], case_id=case_id,
        region=case.get("REGION") or settings.default_region,
        policy=policy, vector_backend=settings.vector_backend,
    )
    repo.save_case_file(case_file)
    repo.save_citations(case_file.case_file_id, case_file.source_provenance)
    record_event(
        repo, case_id=case_id, event_type=AuditEventType.CASE_FILE_ASSEMBLED, actor_type=ActorType.SYSTEM,
        actor_id="case_file_assembler", object_type="CASE_FILE", object_id=case_file.case_file_id,
        details={"source_coverage": case_file.source_coverage, "citation_count": len(case_file.source_provenance)},
    )
    response = {"backend": repo.backend_label(), "case_file": case_file.model_dump()}
    if idempotency_key:
        repo.store_idempotent_response(idempotency_key, endpoint, req_hash, response)
    return response


@router.post("/cases/{case_id}/drafts")
def create_draft(
    case_id: str,
    body: DraftRequest,
    repo: Repository = Depends(get_repo),
):
    """Persistence passthrough — Track B's generation endpoint
    (POST /cases/{id}/explanations) calls this after producing a cited
    narrative. Also usable for a manual investigator draft.
    """
    if repo.get_case(case_id) is None:
        raise NotFoundError(f"case_id {case_id!r} not found")
    draft = repo.save_draft(
        case_id=case_id, content=body.content, generation_id=body.generation_id,
        prompt_version=body.prompt_version, model_version=body.model_version,
        created_by_type=body.created_by_type, verification_status=body.verification_status,
    )
    record_event(
        repo, case_id=case_id, event_type=AuditEventType.DRAFT_CREATED,
        actor_type=ActorType.HUMAN if body.created_by_type == "human" else ActorType.AGENT,
        actor_id=body.generation_id, object_type="NARRATIVE_DRAFT", object_id=draft["DRAFT_ID"],
        details={"draft_version": draft["DRAFT_VERSION"]},
    )
    return {"backend": repo.backend_label(), "draft": draft}


@router.get("/cases/{case_id}/drafts/latest")
def get_latest_draft(case_id: str, repo: Repository = Depends(get_repo)):
    draft = repo.get_latest_draft(case_id)
    if draft is None:
        raise NotFoundError(f"no drafts for case_id {case_id!r}")
    return {"backend": repo.backend_label(), "draft": draft}


# -- decisions (A5, CLAUDE.md §14 "Human accountability") -----------------------


@router.post("/cases/{case_id}/decisions")
def create_decision(
    case_id: str,
    body: DecisionRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    repo: Repository = Depends(get_repo),
):
    """Only a human ever produces a Decision (CLAUDE.md §14). `attested` must
    be true — an unattested decision is refused rather than silently recorded,
    matching the SBPA workflow's "Require investigator attestation" step.
    """
    endpoint = "POST /cases/{id}/decisions"
    req_hash = compute_request_hash({"case_id": case_id, **body.model_dump()})
    if idempotency_key:
        cached = check_idempotency(repo, idempotency_key, endpoint, req_hash)
        if cached is not None:
            return cached

    if repo.get_case(case_id) is None:
        raise NotFoundError(f"case_id {case_id!r} not found")

    try:
        decision_type = DecisionType(body.decision_type)
    except ValueError:
        raise AppError(
            "INVALID_DECISION_TYPE",
            f"decision_type must be one of {[d.value for d in DecisionType]}",
            status_code=422,
        )

    if not body.attested:
        raise AppError(
            "ATTESTATION_REQUIRED",
            "Human decisions require attestation before they are recorded (CLAUDE.md §14).",
            status_code=422,
        )

    decision = Decision(
        decision_id=str(uuid.uuid4()),
        case_id=case_id,
        decision_type=decision_type,
        rationale=body.rationale,
        decided_by=body.decided_by,
        attested=body.attested,
        decided_at=datetime.now(timezone.utc),
    )
    repo.save_decision(decision)
    record_event(
        repo, case_id=case_id, event_type=AuditEventType.DECISION_RECORDED, actor_type=ActorType.HUMAN,
        actor_id=body.decided_by, object_type="DECISION", object_id=decision.decision_id,
        details={"decision_type": decision.decision_type.value, "attested": decision.attested},
    )
    response = {"backend": repo.backend_label(), "decision": decision.model_dump()}
    if idempotency_key:
        repo.store_idempotent_response(idempotency_key, endpoint, req_hash, response)
    return response


@router.get("/cases/{case_id}/decisions")
def list_case_decisions(case_id: str, repo: Repository = Depends(get_repo)):
    if repo.get_case(case_id) is None:
        raise NotFoundError(f"case_id {case_id!r} not found")
    decisions = repo.list_decisions(case_id)
    return {"backend": repo.backend_label(), "count": len(decisions), "decisions": [d.model_dump() for d in decisions]}


# -- audit (A5) -------------------------------------------------------------------


@router.get("/cases/{case_id}/audit-events")
def get_audit_events(case_id: str, repo: Repository = Depends(get_repo)):
    if repo.get_case(case_id) is None:
        raise NotFoundError(f"case_id {case_id!r} not found")
    events = repo.list_audit_events(case_id)
    return {
        "backend": repo.backend_label(),
        "count": len(events),
        "audit_events": [e.model_dump() for e in events],
    }


# -- human-review workflow (A5 endpoints; B5 wires the SAP Build Process ----------
# -- Automation trigger/callback to these two routes) -----------------------------


@router.post("/cases/{case_id}/review-workflows")
def start_review_workflow(
    case_id: str,
    body: ReviewWorkflowRequest = ReviewWorkflowRequest(),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    repo: Repository = Depends(get_repo),
):
    """Starts one human-review workflow instance for the case. `is_fallback`
    is set honestly from configuration at creation time (CLAUDE.md §18) — the
    live SAP Build client (Track B / B5) later calls PATCH on this resource
    with the real `external_instance_id`, which flips it to False.
    """
    endpoint = "POST /cases/{id}/review-workflows"
    req_hash = compute_request_hash({"case_id": case_id, **body.model_dump()})
    if idempotency_key:
        cached = check_idempotency(repo, idempotency_key, endpoint, req_hash)
        if cached is not None:
            return cached

    if repo.get_case(case_id) is None:
        raise NotFoundError(f"case_id {case_id!r} not found")

    settings = get_settings()
    is_fallback = settings.workflow_backend != "sap_build" or not settings.sap_build_api_base_url
    workflow = WorkflowInstance(
        workflow_id=str(uuid.uuid4()),
        case_id=case_id,
        external_instance_id=None,
        status=WorkflowStatus.SENIOR_REVIEW if body.senior_review else WorkflowStatus.PENDING,
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        is_fallback=is_fallback,
    )
    repo.save_workflow_instance(workflow)
    record_event(
        repo, case_id=case_id, event_type=AuditEventType.WORKFLOW_STARTED, actor_type=ActorType.SYSTEM,
        actor_id="review_workflow_starter", object_type="WORKFLOW_INSTANCE", object_id=workflow.workflow_id,
        details={"draft_id": body.draft_id, "senior_review": body.senior_review, "is_fallback": is_fallback},
    )
    response = {"backend": repo.backend_label(), "workflow": workflow.model_dump()}
    if idempotency_key:
        repo.store_idempotent_response(idempotency_key, endpoint, req_hash, response)
    return response


@router.patch("/cases/{case_id}/review-workflows")
def transition_review_workflow(
    case_id: str,
    body: ReviewWorkflowTransitionRequest,
    repo: Repository = Depends(get_repo),
):
    """Callback target for the SBPA trigger (or the local fallback
    state machine) to report a status transition on the latest workflow
    instance for this case. Passing `external_instance_id` here is what
    proves the live workflow ran and flips `is_fallback` to False.
    """
    if repo.get_case(case_id) is None:
        raise NotFoundError(f"case_id {case_id!r} not found")
    current = repo.get_latest_workflow_instance(case_id)
    if current is None:
        raise NotFoundError(f"no review workflow started for case_id {case_id!r}")

    try:
        new_status = WorkflowStatus(body.status)
    except ValueError:
        raise AppError(
            "INVALID_WORKFLOW_STATUS",
            f"status must be one of {[s.value for s in WorkflowStatus]}",
            status_code=422,
        )

    is_terminal = new_status in (WorkflowStatus.APPROVED, WorkflowStatus.RETURNED)
    updated = WorkflowInstance(
        workflow_id=current.workflow_id,
        case_id=case_id,
        external_instance_id=body.external_instance_id or current.external_instance_id,
        status=new_status,
        started_at=current.started_at,
        completed_at=datetime.now(timezone.utc) if is_terminal else current.completed_at,
        is_fallback=current.is_fallback if body.external_instance_id is None else False,
    )
    repo.save_workflow_instance(updated)
    record_event(
        repo, case_id=case_id, event_type=AuditEventType.WORKFLOW_TRANSITIONED, actor_type=ActorType.SYSTEM,
        actor_id="review_workflow_callback", object_type="WORKFLOW_INSTANCE", object_id=updated.workflow_id,
        details={"from": current.status.value, "to": new_status.value},
    )
    return {"backend": repo.backend_label(), "workflow": updated.model_dump()}
