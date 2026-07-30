"""SQLite-backed Repository — CLAUDE.md §18 documented fallback for HANA Cloud.

Used whenever `DATA_BACKEND=local` (the default until the HANA credentials
file is available in this environment). Same interface as `HanaRepository`;
callers must not need to know which one is active except via
`backend_label()`, which every API response surfaces so the UI/tests can
assert the fallback is honestly labelled rather than silently passed off as
HANA.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from trustsphere.domain.alerts import (
    AlertFactorInputs,
    AlertSummary,
    ComplexityInputs,
    ScoreResult,
    UrgencyTier,
    ComplexityBand,
    FactorResult,
    HardOverrideResult,
)
from trustsphere.domain.cases import CaseFile
from trustsphere.domain.citations import Citation
from trustsphere.domain.decisions import AuditEvent, Decision, WorkflowInstance
from trustsphere.persistence.base import Repository

_SCHEMA_PATH = Path(__file__).with_name("local_schema.sql")


def _jsonable(obj: Any) -> Any:
    """Encode exactly like FastAPI's response path (`jsonable_encoder`) so a
    replayed idempotent response is byte-for-byte identical to the original
    live response (e.g. datetimes as ISO-8601 with 'T', not `str(datetime)`'s
    space separator) — CLAUDE.md §23 "idempotency keys on create/start
    operations" means the replay, not just the first call.
    """
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(obj)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class LocalSQLiteRepository(Repository):
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        sql = _SCHEMA_PATH.read_text()
        self.conn.executescript(sql)
        self.conn.commit()

    def backend_label(self) -> str:
        return "local_sqlite_fallback"

    def health_check(self) -> dict[str, Any]:
        cur = self.conn.execute("SELECT COUNT(*) FROM RISK_ALERTS")
        n_alerts = cur.fetchone()[0]
        return {
            "backend": self.backend_label(),
            "ok": True,
            "alert_count": n_alerts,
            "db_path": self.db_path,
        }

    # -- alerts -----------------------------------------------------------
    def list_alerts(
        self, status: str | None = None, limit: int = 200, offset: int = 0
    ) -> list[AlertSummary]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM RISK_ALERTS WHERE STATUS = ? ORDER BY CREATED_AT ASC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM RISK_ALERTS ORDER BY CREATED_AT ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_alert_summary(r) for r in rows]

    def get_alert(self, alert_id: str) -> AlertSummary | None:
        row = self.conn.execute(
            "SELECT * FROM RISK_ALERTS WHERE ALERT_ID = ?", (alert_id,)
        ).fetchone()
        return self._row_to_alert_summary(row) if row else None

    @staticmethod
    def _row_to_alert_summary(row: sqlite3.Row) -> AlertSummary:
        return AlertSummary(
            alert_id=row["ALERT_ID"],
            company_id=row["COMPANY_ID"],
            transaction_id=row["TRANSACTION_ID"],
            alert_type=row["ALERT_TYPE"],
            alert_priority=row["ALERT_PRIORITY"],
            status=row["STATUS"],
            created_at=_parse_dt(row["CREATED_AT"]),
            sla_due_at=_parse_dt(row["SLA_DUE_AT"]),
            source_system="RISK_ALERTS",
            dq_flag=row["ALERT_DQ_FLAG"],
        )

    def get_alert_factor_inputs(self, alert_id: str, as_of: datetime) -> AlertFactorInputs:
        row = self.conn.execute(
            """
            SELECT a.ALERT_ID, a.COMPANY_ID, a.TRANSACTION_ID, a.ALERT_TYPE, a.ALERT_PRIORITY,
                   a.CREATED_AT, a.SLA_DUE_AT,
                   c.KYC_EFFECTIVE_STATUS, c.RESTRICTED_FLAG,
                   crp.COMPOSITE_RISK_SCORE, crp.COUNTRY_RISK_SCORE, crp.RISK_TIER,
                   t.AMOUNT_USD, t.IS_CROSS_BORDER
            FROM RISK_ALERTS a
            LEFT JOIN COMPANIES c ON a.COMPANY_ID = c.COMPANY_ID
            LEFT JOIN COMPANY_RISK_PROFILES crp ON a.COMPANY_ID = crp.COMPANY_ID
            LEFT JOIN TRANSACTIONS t ON a.TRANSACTION_ID = t.TRANSACTION_ID
            WHERE a.ALERT_ID = ?
            """,
            (alert_id,),
        ).fetchone()

        unresolved: list[str] = []
        if row is None:
            unresolved.append(f"RISK_ALERTS row not found for alert_id={alert_id}")
            return AlertFactorInputs(alert_id=alert_id, unresolved_fields=unresolved)

        baseline_row = self.conn.execute(
            "SELECT AVG_AMOUNT_USD FROM TRANSACTION_BASELINES WHERE COMPANY_ID = ? "
            "ORDER BY PERIOD_START DESC LIMIT 1",
            (row["COMPANY_ID"],),
        ).fetchone()
        baseline_avg = Decimal(str(baseline_row["AVG_AMOUNT_USD"])) if baseline_row and baseline_row["AVG_AMOUNT_USD"] is not None else None
        if baseline_avg is None:
            unresolved.append("TRANSACTION_BASELINES not found for company")

        sanctions_count = self.conn.execute(
            "SELECT COUNT(*) FROM SANCTIONS_LISTS WHERE COMPANY_ID = ?", (row["COMPANY_ID"],)
        ).fetchone()[0]

        prior_case_count = self.conn.execute(
            "SELECT COUNT(*) FROM COMPLIANCE_CASES WHERE COMPANY_ID = ?", (row["COMPANY_ID"],)
        ).fetchone()[0]

        created_at = _parse_dt(row["CREATED_AT"])
        sla_due_at = _parse_dt(row["SLA_DUE_AT"])
        hours_remaining = None
        if sla_due_at is not None:
            hours_remaining = (sla_due_at - as_of).total_seconds() / 3600.0
        else:
            unresolved.append("SLA_DUE_AT missing")

        return AlertFactorInputs(
            alert_id=alert_id,
            alert_priority=row["ALERT_PRIORITY"],
            alert_type=row["ALERT_TYPE"],
            composite_risk_score=Decimal(str(row["COMPOSITE_RISK_SCORE"])) if row["COMPOSITE_RISK_SCORE"] is not None else None,
            kyc_effective_status=row["KYC_EFFECTIVE_STATUS"],
            country_risk_score=Decimal(str(row["COUNTRY_RISK_SCORE"])) if row["COUNTRY_RISK_SCORE"] is not None else None,
            risk_tier=row["RISK_TIER"],
            is_cross_border=bool(row["IS_CROSS_BORDER"]) if row["IS_CROSS_BORDER"] is not None else None,
            created_at=created_at,
            sla_due_at=sla_due_at,
            as_of=as_of,
            hours_remaining_to_sla=hours_remaining,
            amount_usd=Decimal(str(row["AMOUNT_USD"])) if row["AMOUNT_USD"] is not None else None,
            baseline_avg_amount_usd=baseline_avg,
            sanctions_match=sanctions_count > 0,
            terrorist_financing_flag=(row["ALERT_TYPE"] == "TERRORIST_FINANCING"),
            restricted_customer_flag=bool(row["RESTRICTED_FLAG"]) if row["RESTRICTED_FLAG"] is not None else False,
            has_prior_escalated_case=prior_case_count > 0,
            unresolved_fields=unresolved,
        )

    def get_complexity_inputs(self, alert_id: str) -> ComplexityInputs:
        row = self.conn.execute(
            "SELECT COMPANY_ID, TRANSACTION_ID FROM RISK_ALERTS WHERE ALERT_ID = ?", (alert_id,)
        ).fetchone()
        if row is None:
            return ComplexityInputs(alert_id=alert_id)
        company_id, txn_id = row["COMPANY_ID"], row["TRANSACTION_ID"]

        entities: set[str] = {company_id} if company_id else set()
        countries: set[str] = set()

        if txn_id:
            t = self.conn.execute(
                "SELECT ORIGINATOR_COMPANY_ID, BENEFICIARY_COMPANY_ID, ORIGINATING_COUNTRY_ID, "
                "DESTINATION_COUNTRY_ID FROM TRANSACTIONS WHERE TRANSACTION_ID = ?",
                (txn_id,),
            ).fetchone()
            if t:
                for c in (t["ORIGINATOR_COMPANY_ID"], t["BENEFICIARY_COMPANY_ID"]):
                    if c:
                        entities.add(c)
                for cty in (t["ORIGINATING_COUNTRY_ID"], t["DESTINATION_COUNTRY_ID"]):
                    if cty:
                        countries.add(cty)

        owners = self.conn.execute(
            "SELECT OWNER_NAME FROM COMPANY_BENEFICIAL_OWNERS WHERE COMPANY_ID = ?", (company_id,)
        ).fetchall()
        entities.update(f"owner:{o['OWNER_NAME']}" for o in owners)

        kyc_row = self.conn.execute(
            "SELECT KYC_EFFECTIVE_STATUS FROM COMPANIES WHERE COMPANY_ID = ?", (company_id,)
        ).fetchone()
        missing_kyc = 1 if kyc_row and kyc_row["KYC_EFFECTIVE_STATUS"] != "VERIFIED" else 0

        related_alerts = self.conn.execute(
            "SELECT COUNT(*) FROM RISK_ALERTS WHERE COMPANY_ID = ? AND ALERT_ID != ?",
            (company_id, alert_id),
        ).fetchone()[0]

        txn_count = self.conn.execute(
            "SELECT COUNT(*) FROM TRANSACTIONS WHERE ORIGINATOR_COMPANY_ID = ? OR BENEFICIARY_COMPANY_ID = ?",
            (company_id, company_id),
        ).fetchone()[0]
        volume_band = 0 if txn_count < 10 else 1 if txn_count < 50 else 2 if txn_count < 200 else 3

        return ComplexityInputs(
            alert_id=alert_id,
            entity_count=len(entities),
            jurisdiction_count=len(countries),
            missing_kyc_count=missing_kyc,
            source_system_count=1,
            related_alert_count=related_alerts,
            transaction_volume_band=volume_band,
        )

    # -- business-fact reads for CaseFile assembly (A3) ------------------------
    def get_customer_profile(self, company_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM COMPANIES WHERE COMPANY_ID = ?", (company_id,)).fetchone()
        return dict(row) if row else None

    def get_counterparty_profiles(self, alert_id: str) -> list[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT COMPANY_ID, TRANSACTION_ID FROM RISK_ALERTS WHERE ALERT_ID = ?", (alert_id,)
        ).fetchone()
        if row is None or row["TRANSACTION_ID"] is None:
            return []
        t = self.conn.execute(
            "SELECT BENEFICIARY_NAME, BENEFICIARY_COMPANY_ID, BENEFICIARY_COUNTRY_ID, "
            "ORIGINATOR_COMPANY_ID FROM TRANSACTIONS WHERE TRANSACTION_ID = ?",
            (row["TRANSACTION_ID"],),
        ).fetchone()
        if t is None:
            return []
        results = []
        this_company = row["COMPANY_ID"]
        if t["BENEFICIARY_COMPANY_ID"] and t["BENEFICIARY_COMPANY_ID"] != this_company:
            n = self.conn.execute(
                "SELECT COUNT(*) FROM TRANSACTIONS WHERE BENEFICIARY_COMPANY_ID = ? OR ORIGINATOR_COMPANY_ID = ?",
                (t["BENEFICIARY_COMPANY_ID"], t["BENEFICIARY_COMPANY_ID"]),
            ).fetchone()[0]
            results.append({
                "counterparty_label": t["BENEFICIARY_NAME"] or t["BENEFICIARY_COMPANY_ID"],
                "jurisdiction_country_id": t["BENEFICIARY_COUNTRY_ID"],
                "appearance_count": n,
            })
        return results

    def get_transaction_timeline(self, company_id: str, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM TRANSACTIONS WHERE ORIGINATOR_COMPANY_ID = ? OR BENEFICIARY_COMPANY_ID = ? "
            "ORDER BY INITIATED_AT DESC LIMIT ?",
            (company_id, company_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_related_alerts(self, alert_id: str, limit: int = 10) -> list[dict[str, Any]]:
        row = self.conn.execute("SELECT COMPANY_ID FROM RISK_ALERTS WHERE ALERT_ID = ?", (alert_id,)).fetchone()
        if row is None:
            return []
        rows = self.conn.execute(
            "SELECT ALERT_ID, ALERT_TYPE, STATUS, COMPANY_ID FROM RISK_ALERTS "
            "WHERE COMPANY_ID = ? AND ALERT_ID != ? ORDER BY CREATED_AT DESC LIMIT ?",
            (row["COMPANY_ID"], alert_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_beneficial_owners(self, company_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM COMPANY_BENEFICIAL_OWNERS WHERE COMPANY_ID = ?", (company_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_policy_passages(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM POLICY_PASSAGES").fetchall()
        return [dict(r) for r in rows]

    def list_sla_training_alert_ids(self) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            """SELECT ALERT_ID, RESOLUTION_HOURS FROM RISK_ALERTS
               WHERE STATUS IN ('CLOSED_TRUE', 'CLOSED_FALSE')
                 AND RESOLUTION_HOURS IS NOT NULL
                 AND (ALERT_DQ_FLAG IS NULL OR ALERT_DQ_FLAG != 'RESOLVED_BEFORE_CREATED')"""
        ).fetchall()
        return [(r["ALERT_ID"], r["RESOLUTION_HOURS"]) for r in rows]

    # -- scoring ------------------------------------------------------------
    def save_score(self, score: ScoreResult) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM PRIORITY_SCORES WHERE ALERT_ID = ?", (score.alert_id,)
            )
            self.conn.execute(
                """INSERT INTO PRIORITY_SCORES
                   (ALERT_ID, URGENCY_SCORE, URGENCY_TIER, HARD_OVERRIDE_CODE, COMPLEXITY_BAND,
                    COMPLEXITY_POINTS, POLICY_VERSION, CALCULATED_AT, CAVEATS_JSON)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    score.alert_id,
                    score.urgency_score,
                    score.urgency_tier.value,
                    score.hard_override.code if score.hard_override else None,
                    score.complexity_band.value,
                    score.complexity_points,
                    score.policy_version,
                    _iso(score.calculated_at),
                    json.dumps(score.caveats),
                ),
            )
            self.conn.execute("DELETE FROM ALERT_FACTORS WHERE ALERT_ID = ?", (score.alert_id,))
            for f in score.factors:
                self.conn.execute(
                    """INSERT INTO ALERT_FACTORS
                       (ALERT_ID, FACTOR_CODE, RAW_VALUE, NORMALISED_VALUE, WEIGHT, WEIGHTED_POINTS,
                        REASON_CODE, POLICY_VERSION, CALCULATED_AT)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        score.alert_id,
                        f.factor_code,
                        f.raw_value,
                        f.normalised_value,
                        f.weight,
                        f.weighted_points,
                        f.reason_code,
                        f.policy_version,
                        _iso(score.calculated_at),
                    ),
                )

    def get_latest_score(self, alert_id: str) -> ScoreResult | None:
        row = self.conn.execute(
            "SELECT * FROM PRIORITY_SCORES WHERE ALERT_ID = ?", (alert_id,)
        ).fetchone()
        if row is None:
            return None
        factor_rows = self.conn.execute(
            "SELECT * FROM ALERT_FACTORS WHERE ALERT_ID = ?", (alert_id,)
        ).fetchall()
        factors = [
            FactorResult(
                factor_code=fr["FACTOR_CODE"],
                raw_value=fr["RAW_VALUE"],
                normalised_value=fr["NORMALISED_VALUE"],
                weight=fr["WEIGHT"],
                weighted_points=fr["WEIGHTED_POINTS"],
                reason_code=fr["REASON_CODE"],
                policy_version=fr["POLICY_VERSION"],
            )
            for fr in factor_rows
        ]
        hard_override = None
        if row["HARD_OVERRIDE_CODE"]:
            hard_override = HardOverrideResult(
                code=row["HARD_OVERRIDE_CODE"],
                forced_tier=UrgencyTier(row["URGENCY_TIER"]),
                reason=row["HARD_OVERRIDE_CODE"],
            )
        return ScoreResult(
            alert_id=alert_id,
            urgency_score=row["URGENCY_SCORE"],
            urgency_tier=UrgencyTier(row["URGENCY_TIER"]),
            hard_override=hard_override,
            factors=factors,
            complexity_band=ComplexityBand(row["COMPLEXITY_BAND"]),
            complexity_points=row["COMPLEXITY_POINTS"],
            policy_version=row["POLICY_VERSION"],
            calculated_at=_parse_dt(row["CALCULATED_AT"]),
            caveats=json.loads(row["CAVEATS_JSON"]) if row["CAVEATS_JSON"] else [],
        )

    def list_scored_alerts_ordered(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT a.ALERT_ID, a.ALERT_TYPE, a.STATUS, a.SLA_DUE_AT,
                   p.URGENCY_SCORE, p.URGENCY_TIER, p.HARD_OVERRIDE_CODE,
                   p.COMPLEXITY_BAND, p.COMPLEXITY_POINTS, p.CALCULATED_AT
            FROM RISK_ALERTS a
            JOIN PRIORITY_SCORES p ON a.ALERT_ID = p.ALERT_ID
            WHERE a.STATUS NOT LIKE 'CLOSED%'
            ORDER BY
                CASE WHEN p.HARD_OVERRIDE_CODE IS NOT NULL THEN 0 ELSE 1 END ASC,
                CASE p.URGENCY_TIER
                    WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3
                END ASC,
                a.SLA_DUE_AT ASC,
                p.URGENCY_SCORE DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- predictive SLA -----------------------------------------------------
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
        with self.conn:
            self.conn.execute(
                """INSERT INTO PREDICTIVE_SCORES
                   (ALERT_ID, PREDICTION_TYPE, PREDICTION_VALUE, MODEL_NAME, MODEL_VERSION,
                    FEATURE_SNAPSHOT_ID, ADVISORY_ONLY, SCORED_AT, EXTRA_JSON)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    alert_id,
                    prediction_type,
                    prediction_value,
                    model_name,
                    model_version,
                    feature_snapshot_id,
                    _iso(scored_at),
                    json.dumps(extra or {}),
                ),
            )

    def get_latest_predictive_score(self, alert_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM PREDICTIVE_SCORES WHERE ALERT_ID = ? ORDER BY SCORED_AT DESC LIMIT 1",
            (alert_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["EXTRA_JSON"] = json.loads(d["EXTRA_JSON"]) if d.get("EXTRA_JSON") else {}
        return d

    # -- cases / case files ---------------------------------------------------
    def get_or_create_case(self, alert_id: str, assigned_team: str, region: str) -> str:
        row = self.conn.execute(
            "SELECT CASE_ID FROM CASES WHERE ALERT_ID = ?", (alert_id,)
        ).fetchone()
        if row:
            return row["CASE_ID"]
        case_id = f"CASE-{alert_id}"
        now = _iso(_now())
        with self.conn:
            self.conn.execute(
                """INSERT INTO CASES (CASE_ID, ALERT_ID, ASSIGNED_TEAM, STATUS, CREATED_AT, UPDATED_AT, REGION)
                   VALUES (?, ?, ?, 'OPEN', ?, ?, ?)""",
                (case_id, alert_id, assigned_team, now, now, region),
            )
        return case_id

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM CASES WHERE CASE_ID = ?", (case_id,)).fetchone()
        return dict(row) if row else None

    def save_case_file(self, case_file: CaseFile) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO CASE_FILES
                   (CASE_FILE_ID, CASE_ID, SCHEMA_VERSION, CONTENT_JSON, ASSEMBLED_AT, SOURCE_COVERAGE)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    case_file.case_file_id,
                    case_file.case_id,
                    case_file.schema_version,
                    case_file.model_dump_json(),
                    _iso(case_file.assembled_at),
                    case_file.source_coverage,
                ),
            )

    def get_latest_case_file(self, case_id: str) -> CaseFile | None:
        row = self.conn.execute(
            "SELECT * FROM CASE_FILES WHERE CASE_ID = ? ORDER BY ASSEMBLED_AT DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        if row is None:
            return None
        return CaseFile.model_validate_json(row["CONTENT_JSON"])

    def save_citations(self, case_file_id: str, citations: list[Citation]) -> None:
        with self.conn:
            for c in citations:
                self.conn.execute(
                    """INSERT INTO SOURCE_CITATIONS
                       (CITATION_ID, CASE_FILE_ID, SOURCE_TYPE, SOURCE_ID, SOURCE_LOCATOR,
                        SOURCE_VERSION, RETRIEVED_AT, REGION, PERMISSION_SCOPE)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        c.citation_id,
                        case_file_id,
                        c.source_type.value,
                        c.source_id,
                        c.source_locator,
                        c.source_version,
                        _iso(c.retrieved_at),
                        c.region,
                        c.permission_scope,
                    ),
                )

    # -- narrative drafts -----------------------------------------------------
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
        prev = self.conn.execute(
            "SELECT MAX(DRAFT_VERSION) AS mx FROM NARRATIVE_DRAFTS WHERE CASE_ID = ?", (case_id,)
        ).fetchone()
        next_version = (prev["mx"] or 0) + 1
        draft_id = f"DRAFT-{case_id}-{next_version}"
        now = _iso(_now())
        with self.conn:
            self.conn.execute(
                """INSERT INTO NARRATIVE_DRAFTS
                   (DRAFT_ID, CASE_ID, DRAFT_VERSION, CONTENT, GENERATION_ID, PROMPT_VERSION,
                    MODEL_VERSION, CREATED_BY_TYPE, CREATED_AT, VERIFICATION_STATUS)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    draft_id, case_id, next_version, content, generation_id, prompt_version,
                    model_version, created_by_type, now, verification_status,
                ),
            )
        return self.get_latest_draft(case_id)

    def get_latest_draft(self, case_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM NARRATIVE_DRAFTS WHERE CASE_ID = ? ORDER BY DRAFT_VERSION DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        return dict(row) if row else None

    # -- decisions / workflow / audit -----------------------------------------
    def save_decision(self, decision: Decision) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO DECISIONS
                   (DECISION_ID, CASE_ID, DECISION_TYPE, RATIONALE, DECIDED_BY, ATTESTED, DECIDED_AT)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.decision_id, decision.case_id, decision.decision_type.value,
                    decision.rationale, decision.decided_by, int(decision.attested),
                    _iso(decision.decided_at),
                ),
            )

    def list_decisions(self, case_id: str) -> list[Decision]:
        rows = self.conn.execute(
            "SELECT * FROM DECISIONS WHERE CASE_ID = ? ORDER BY DECIDED_AT ASC", (case_id,)
        ).fetchall()
        from trustsphere.domain.decisions import DecisionType

        return [
            Decision(
                decision_id=r["DECISION_ID"],
                case_id=r["CASE_ID"],
                decision_type=DecisionType(r["DECISION_TYPE"]),
                rationale=r["RATIONALE"],
                decided_by=r["DECIDED_BY"],
                attested=bool(r["ATTESTED"]),
                decided_at=_parse_dt(r["DECIDED_AT"]),
            )
            for r in rows
        ]

    def save_workflow_instance(self, workflow: WorkflowInstance) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO WORKFLOW_INSTANCES
                   (WORKFLOW_ID, CASE_ID, EXTERNAL_INSTANCE_ID, STATUS, STARTED_AT, COMPLETED_AT, IS_FALLBACK)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(WORKFLOW_ID) DO UPDATE SET
                     STATUS=excluded.STATUS, COMPLETED_AT=excluded.COMPLETED_AT,
                     EXTERNAL_INSTANCE_ID=excluded.EXTERNAL_INSTANCE_ID,
                     IS_FALLBACK=excluded.IS_FALLBACK""",
                (
                    workflow.workflow_id, workflow.case_id, workflow.external_instance_id,
                    workflow.status.value, _iso(workflow.started_at), _iso(workflow.completed_at),
                    int(workflow.is_fallback),
                ),
            )

    def get_latest_workflow_instance(self, case_id: str) -> WorkflowInstance | None:
        row = self.conn.execute(
            "SELECT * FROM WORKFLOW_INSTANCES WHERE CASE_ID = ? ORDER BY STARTED_AT DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        if row is None:
            return None
        from trustsphere.domain.decisions import WorkflowStatus

        return WorkflowInstance(
            workflow_id=row["WORKFLOW_ID"],
            case_id=row["CASE_ID"],
            external_instance_id=row["EXTERNAL_INSTANCE_ID"],
            status=WorkflowStatus(row["STATUS"]),
            started_at=_parse_dt(row["STARTED_AT"]),
            completed_at=_parse_dt(row["COMPLETED_AT"]),
            is_fallback=bool(row["IS_FALLBACK"]),
        )

    def append_audit_event(self, event: AuditEvent) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO AUDIT_EVENTS
                   (EVENT_ID, CASE_ID, EVENT_TYPE, ACTOR_TYPE, ACTOR_ID, OBJECT_TYPE, OBJECT_ID,
                    DETAILS_JSON, OCCURRED_AT, CORRELATION_ID)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id, event.case_id, event.event_type.value, event.actor_type.value,
                    event.actor_id, event.object_type, event.object_id,
                    json.dumps(event.details), _iso(event.occurred_at), event.correlation_id,
                ),
            )

    def list_audit_events(self, case_id: str) -> list[AuditEvent]:
        rows = self.conn.execute(
            "SELECT * FROM AUDIT_EVENTS WHERE CASE_ID = ? ORDER BY OCCURRED_AT ASC", (case_id,)
        ).fetchall()
        from trustsphere.domain.decisions import ActorType, AuditEventType

        return [
            AuditEvent(
                event_id=r["EVENT_ID"],
                case_id=r["CASE_ID"],
                event_type=AuditEventType(r["EVENT_TYPE"]),
                actor_type=ActorType(r["ACTOR_TYPE"]),
                actor_id=r["ACTOR_ID"],
                object_type=r["OBJECT_TYPE"],
                object_id=r["OBJECT_ID"],
                details=json.loads(r["DETAILS_JSON"]),
                occurred_at=_parse_dt(r["OCCURRED_AT"]),
                correlation_id=r["CORRELATION_ID"],
            )
            for r in rows
        ]

    # -- idempotency ----------------------------------------------------------
    def check_and_store_idempotency_key(
        self, key: str, endpoint: str, request_hash: str
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT REQUEST_HASH, RESPONSE_JSON FROM IDEMPOTENCY_KEYS WHERE IDEMPOTENCY_KEY = ? AND ENDPOINT = ?",
            (key, endpoint),
        ).fetchone()
        if row is None:
            return None
        if row["REQUEST_HASH"] != request_hash:
            raise ValueError(
                f"Idempotency key {key!r} reused with a different request body on {endpoint}"
            )
        return json.loads(row["RESPONSE_JSON"])

    def store_idempotent_response(
        self, key: str, endpoint: str, request_hash: str, response: dict[str, Any]
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO IDEMPOTENCY_KEYS
                   (IDEMPOTENCY_KEY, ENDPOINT, REQUEST_HASH, RESPONSE_JSON, CREATED_AT)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, endpoint, request_hash, json.dumps(_jsonable(response)), _iso(_now())),
            )
