"""Alert and scoring domain types.

`AlertFactorInputs` is the seam between retrieval (which knows real HANA
column names) and the scoring engine (which is pure and schema-agnostic —
CLAUDE.md §23 "Keep domain logic independent of ... vendor SDKs"). Every
field is Optional; a None means the retrieval layer could not populate it,
which the scoring engine turns into a documented default + reason code
rather than an inferred value.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

# The tenant's TRUSTSPHERE_REFERENCE snapshot stores entity ids as INTEGERs
# (e.g. ALERT_ID=19263) while the domain treats ids as opaque strings (the
# local seed uses "ALERT-9001"). Coerce at the model boundary so both
# backends satisfy the same types.
_COERCE_IDS = ConfigDict(coerce_numbers_to_str=True)


class UrgencyTier(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ComplexityBand(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class AlertSummary(BaseModel):
    """Minimal alert identity — what `GET /alerts` returns per row."""
    model_config = _COERCE_IDS

    alert_id: str
    company_id: str | None = None
    transaction_id: str | None = None
    alert_type: str | None = None
    alert_priority: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    sla_due_at: datetime | None = None
    source_system: str = "RISK_ALERTS"
    dq_flag: str | None = None


class AlertFactorInputs(BaseModel):
    """Populated by retrieval/sql.py. Every field carries its own provenance
    note in `field_sources` so a missing value can be explained rather than
    silently defaulted without a trace.
    """
    model_config = _COERCE_IDS

    alert_id: str

    # typology_severity
    alert_priority: str | None = None
    alert_type: str | None = None

    # entity_risk
    composite_risk_score: Decimal | None = None
    kyc_effective_status: str | None = None

    # jurisdiction_exposure
    country_risk_score: Decimal | None = None
    risk_tier: str | None = None
    is_cross_border: bool | None = None

    # sla_age_proximity
    created_at: datetime | None = None
    sla_due_at: datetime | None = None
    as_of: datetime | None = None
    hours_remaining_to_sla: float | None = None

    # transaction_materiality
    amount_usd: Decimal | None = None
    baseline_avg_amount_usd: Decimal | None = None

    # hard-override inputs (see config/scoring_policy.yaml `hard_overrides`)
    sanctions_match: bool | None = None
    terrorist_financing_flag: bool | None = None
    restricted_customer_flag: bool | None = None
    has_prior_escalated_case: bool | None = None

    # provenance / caveats collected while assembling this object
    unresolved_fields: list[str] = []


class FactorResult(BaseModel):
    factor_code: str
    raw_value: str | None = None
    normalised_value: float
    weight: float
    weighted_points: float
    reason_code: str
    policy_version: str


class HardOverrideResult(BaseModel):
    code: str
    forced_tier: UrgencyTier
    reason: str


class ScoreResult(BaseModel):
    model_config = _COERCE_IDS
    alert_id: str
    urgency_score: float
    urgency_tier: UrgencyTier
    hard_override: HardOverrideResult | None = None
    factors: list[FactorResult]
    complexity_band: ComplexityBand
    complexity_points: int
    policy_version: str
    calculated_at: datetime
    caveats: list[str] = []


class ComplexityInputs(BaseModel):
    model_config = _COERCE_IDS
    alert_id: str
    entity_count: int = 0
    jurisdiction_count: int = 0
    missing_kyc_count: int = 0
    source_system_count: int = 1
    related_alert_count: int = 0
    transaction_volume_band: int = 0
