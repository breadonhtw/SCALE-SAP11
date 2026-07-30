"""Typed CaseFile (CLAUDE.md §8).

Generation (Track B) receives this typed, permission-checked object — never
raw DB access. Every section is explicit about what it does and does not
contain; `missing_information` is a first-class list, not an absence.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from trustsphere.domain.alerts import (
    _COERCE_IDS,
    ComplexityBand,
    ScoreResult,
    UrgencyTier,
)
from trustsphere.domain.citations import Citation, MissingInformation


class AlertDetails(BaseModel):
    model_config = _COERCE_IDS
    alert_id: str
    alert_type: str | None
    alert_priority: str | None
    status: str | None
    created_at: datetime | None
    sla_due_at: datetime | None
    company_id: str | None
    transaction_id: str | None


class PriorityExplanation(BaseModel):
    urgency_score: float
    urgency_tier: UrgencyTier
    hard_override_code: str | None
    factor_breakdown: list[dict]
    complexity_band: ComplexityBand
    policy_version: str


class PredictiveAdvisory(BaseModel):
    prediction_type: str
    prediction_value: float
    model_name: str
    model_version: str
    advisory_only: bool = True
    label: str = "Advisory — pilot/shadow mode"
    scored_at: datetime


class CustomerProfile(BaseModel):
    model_config = _COERCE_IDS
    company_id: str
    legal_name: str | None
    registration_number: str | None
    lei_code: str | None
    kyc_effective_status: str | None
    kyc_risk_rating: str | None
    client_segment: str | None
    incorporation_country_id: str | None
    headquarters_country_id: str | None


class CounterpartyProfile(BaseModel):
    model_config = _COERCE_IDS
    counterparty_label: str
    jurisdiction_country_id: str | None
    appearance_count: int


class TransactionTimelineEntry(BaseModel):
    model_config = _COERCE_IDS
    transaction_id: str
    occurred_at: datetime | None
    amount_usd: Decimal | None
    currency_original: str | None
    direction: str | None
    origin_country_id: str | None
    destination_country_id: str | None
    is_cross_border: bool | None


class RelationshipEdge(BaseModel):
    model_config = _COERCE_IDS
    relationship_type: str
    source_node: str
    target_node: str
    citation_id: str


class RelatedAlertRef(BaseModel):
    model_config = _COERCE_IDS
    alert_id: str
    alert_type: str | None
    status: str | None
    shared_company_id: str | None


class PolicyPassage(BaseModel):
    model_config = _COERCE_IDS
    document_id: str
    passage_locator: str
    text: str
    similarity_score: float
    citation_id: str


class HistoricalCaseRef(BaseModel):
    model_config = _COERCE_IDS
    case_id: str
    similarity_score: float
    permission_scope: str
    region: str
    citation_id: str


class DataFreshness(BaseModel):
    source_object: str
    retrieved_at: datetime
    source_updated_at: datetime | None = None


class CaseFile(BaseModel):
    """CLAUDE.md §8 section list, implemented verbatim."""

    case_file_id: str
    case_id: str
    schema_version: str = "1.0"
    assembled_at: datetime

    alert_details: AlertDetails
    priority_explanation: PriorityExplanation
    predictive_advisories: list[PredictiveAdvisory] = []
    customer_profile: CustomerProfile | None
    counterparty_profiles: list[CounterpartyProfile] = []
    transaction_timeline: list[TransactionTimelineEntry] = []
    entity_relationships: list[RelationshipEdge] = []
    related_alerts: list[RelatedAlertRef] = []
    policy_context: list[PolicyPassage] = []
    historical_case_references: list[HistoricalCaseRef] = []
    missing_information: list[MissingInformation] = []
    source_provenance: list[Citation] = []
    data_freshness: list[DataFreshness] = []

    source_coverage: float = 0.0
    region: str = "APJ"
