"""Human decision, workflow, and audit types (CLAUDE.md §14 "Human accountability").

Only a human ever produces a Decision. Nothing here allows an agent or
generative step to set `decided_by` to anything but a human identity —
the API layer enforces that, this module just defines the shape.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DecisionType(StrEnum):
    APPROVE_FOR_ESCALATION = "approve_for_escalation"
    RETURN_FOR_EDIT = "return_for_edit"
    REQUEST_INFORMATION = "request_information"


class Decision(BaseModel):
    decision_id: str
    case_id: str
    decision_type: DecisionType
    rationale: str = Field(min_length=1, description="Required — no silent decisions")
    decided_by: str
    attested: bool
    decided_at: datetime


class WorkflowStatus(StrEnum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    SENIOR_REVIEW = "SENIOR_REVIEW"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    INFO_REQUESTED = "INFO_REQUESTED"


class WorkflowInstance(BaseModel):
    workflow_id: str
    case_id: str
    external_instance_id: str | None = None
    status: WorkflowStatus
    started_at: datetime
    completed_at: datetime | None = None
    is_fallback: bool = Field(
        default=True,
        description="True when running the local review-state machine fallback "
        "rather than live SAP Build Process Automation (CLAUDE.md §18).",
    )


class AuditEventType(StrEnum):
    ALERT_SCORED = "ALERT_SCORED"
    SLA_PREDICTED = "SLA_PREDICTED"
    CASE_FILE_ASSEMBLED = "CASE_FILE_ASSEMBLED"
    EXPLANATION_GENERATED = "EXPLANATION_GENERATED"
    DRAFT_CREATED = "DRAFT_CREATED"
    DRAFT_UPDATED = "DRAFT_UPDATED"
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_TRANSITIONED = "WORKFLOW_TRANSITIONED"
    DECISION_RECORDED = "DECISION_RECORDED"


class ActorType(StrEnum):
    HUMAN = "human"
    SYSTEM = "system"
    AGENT = "agent"


class AuditEvent(BaseModel):
    """Append-only. CLAUDE.md §7: application code must not update or delete
    existing audit events — corrections are new events, never mutations.
    """

    event_id: str
    case_id: str
    event_type: AuditEventType
    actor_type: ActorType
    actor_id: str
    object_type: str
    object_id: str
    details: dict = {}
    occurred_at: datetime
    correlation_id: str
