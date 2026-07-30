"""Request/response schemas for endpoints that don't map 1:1 onto a domain model."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScoreAlertRequest(BaseModel):
    as_of: str | None = Field(default=None, description="ISO-8601 timestamp; defaults to now")


class PredictSlaRequest(BaseModel):
    as_of: str | None = None


class AssembleCaseRequest(BaseModel):
    assigned_team: str = "financial-crime-ops"
    region: str = "APJ"


class DecisionRequest(BaseModel):
    decision_type: str
    rationale: str = Field(min_length=1)
    decided_by: str
    attested: bool


class DraftRequest(BaseModel):
    content: str
    generation_id: str = "manual"
    prompt_version: str = "n/a"
    model_version: str = "n/a"
    created_by_type: str = "human"
    verification_status: str = "unverified"


class ReviewWorkflowRequest(BaseModel):
    draft_id: str | None = None
    senior_review: bool = False


class ReviewWorkflowTransitionRequest(BaseModel):
    status: str
    external_instance_id: str | None = None
