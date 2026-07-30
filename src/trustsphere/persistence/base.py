"""Repository interface — the one seam between domain/services and storage.

Both `LocalSQLiteRepository` (dev/demo fallback, no tenant creds needed) and
`HanaRepository` (SAP HANA Cloud, TEAM_11_USER) implement this. Nothing
above this layer may import `hdbcli` or `sqlite3` directly (CLAUDE.md §23
"Keep domain logic independent of ... vendor SDKs").

CLAUDE.md §18: a fallback must preserve the API contract and provenance.
`backend_label()` exists so every response can honestly say which one served
it — never claim HANA execution from the local fallback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from trustsphere.domain.alerts import (
    AlertFactorInputs,
    AlertSummary,
    ComplexityInputs,
    ScoreResult,
)
from trustsphere.domain.cases import CaseFile
from trustsphere.domain.citations import Citation
from trustsphere.domain.decisions import AuditEvent, Decision, WorkflowInstance


class Repository(ABC):
    # -- meta -----------------------------------------------------------
    @abstractmethod
    def backend_label(self) -> str:
        """e.g. 'local_sqlite_fallback' or 'hana_cloud:TEAM_11_USER'."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        ...

    # -- alerts -----------------------------------------------------------
    @abstractmethod
    def list_alerts(
        self, status: str | None = None, limit: int = 200, offset: int = 0
    ) -> list[AlertSummary]:
        ...

    @abstractmethod
    def get_alert(self, alert_id: str) -> AlertSummary | None:
        ...

    @abstractmethod
    def get_alert_factor_inputs(self, alert_id: str, as_of: datetime) -> AlertFactorInputs:
        ...

    @abstractmethod
    def get_complexity_inputs(self, alert_id: str) -> ComplexityInputs:
        ...

    @abstractmethod
    def list_sla_training_alert_ids(self) -> list[tuple[str, float]]:
        """(alert_id, RESOLUTION_HOURS) pairs for CLOSED_TRUE/CLOSED_FALSE
        alerts with a usable resolution duration — i.e. excluding rows
        flagged ALERT_DQ_FLAG='RESOLVED_BEFORE_CREATED' (CLAUDE.md §0,
        docs/data-quality-report.md). Feature vectors are then built per
        alert_id via get_alert_factor_inputs/get_complexity_inputs so
        training and inference share one featurisation path.
        """

    # -- business-fact reads for CaseFile assembly (A3) ------------------------
    @abstractmethod
    def get_customer_profile(self, company_id: str) -> dict[str, Any] | None:
        ...

    @abstractmethod
    def get_counterparty_profiles(self, alert_id: str) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def get_transaction_timeline(self, company_id: str, limit: int = 25) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def get_related_alerts(self, alert_id: str, limit: int = 10) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def get_beneficial_owners(self, company_id: str) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def list_policy_passages(self) -> list[dict[str, Any]]:
        """All rows of POLICY_PASSAGES — used by the local TF-IDF fallback
        ranker. The HANA-native path (retrieval/vector.py HanaVectorRetriever)
        queries VECTOR_EMBEDDING/COSINE_SIMILARITY directly against HANA
        instead of calling this.
        """

    # -- scoring ------------------------------------------------------------
    @abstractmethod
    def save_score(self, score: ScoreResult) -> None:
        ...

    @abstractmethod
    def get_latest_score(self, alert_id: str) -> ScoreResult | None:
        ...

    @abstractmethod
    def count_scored_open_alerts(self) -> int:
        ...

    @abstractmethod
    def list_scored_alerts_ordered(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        """Queue-ordered view: hard overrides first, tier, SLA remaining,
        urgency score, complexity as tie-break (CLAUDE.md §9 "Queue policy").
        """

    # -- predictive SLA -----------------------------------------------------
    @abstractmethod
    def save_predictive_score(
        self,
        alert_id: str,
        prediction_type: str,
        prediction_value: float,
        model_name: str,
        model_version: str,
        feature_snapshot_id: str,
        scored_at: datetime,
        extra: dict | None = None,
    ) -> None:
        ...

    @abstractmethod
    def get_latest_predictive_score(self, alert_id: str) -> dict[str, Any] | None:
        ...

    # -- cases / case files ---------------------------------------------------
    @abstractmethod
    def get_or_create_case(self, alert_id: str, assigned_team: str, region: str) -> str:
        """Idempotent: one case per alert_id."""

    @abstractmethod
    def get_case(self, case_id: str) -> dict[str, Any] | None:
        ...

    @abstractmethod
    def update_case_status(self, case_id: str, status: str) -> None:
        """Reflect a lifecycle transition (e.g. a recorded decision) on the
        case row. Never touches RISK_ALERTS — alert closure belongs to the
        bank's downstream case management, not this prototype (CLAUDE.md §3).
        """

    @abstractmethod
    def save_case_file(self, case_file: CaseFile) -> None:
        ...

    @abstractmethod
    def get_latest_case_file(self, case_id: str) -> CaseFile | None:
        ...

    @abstractmethod
    def save_citations(self, case_file_id: str, citations: list[Citation]) -> None:
        ...

    # -- narrative drafts -----------------------------------------------------
    @abstractmethod
    def save_draft(
        self,
        case_id: str,
        content: str,
        generation_id: str,
        prompt_version: str,
        model_version: str,
        created_by_type: str,
        verification_status: str = "unverified",
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_latest_draft(self, case_id: str) -> dict[str, Any] | None:
        ...

    # -- decisions / workflow / audit -----------------------------------------
    @abstractmethod
    def save_decision(self, decision: Decision) -> None:
        ...

    @abstractmethod
    def list_decisions(self, case_id: str) -> list[Decision]:
        ...

    @abstractmethod
    def save_workflow_instance(self, workflow: WorkflowInstance) -> None:
        ...

    @abstractmethod
    def get_latest_workflow_instance(self, case_id: str) -> WorkflowInstance | None:
        ...

    @abstractmethod
    def append_audit_event(self, event: AuditEvent) -> None:
        """Append-only. Never call UPDATE/DELETE against AUDIT_EVENTS."""

    @abstractmethod
    def list_audit_events(self, case_id: str) -> list[AuditEvent]:
        ...

    # -- idempotency ----------------------------------------------------------
    @abstractmethod
    def check_and_store_idempotency_key(
        self, key: str, endpoint: str, request_hash: str
    ) -> dict[str, Any] | None:
        """Returns the stored response dict if `key` was already used for this
        endpoint+request_hash (replay), else records it and returns None.
        """

    @abstractmethod
    def store_idempotent_response(
        self, key: str, endpoint: str, request_hash: str, response: dict[str, Any]
    ) -> None:
        ...
