"""SAP HANA Cloud Repository — schema TEAM_11_USER (team-11 tenant).

Written to the same interface as `LocalSQLiteRepository` and to the actual
columns confirmed live against the tenant in `scripts/profile_data.py` /
`scripts/clean_data.py` (see docs/data-quality-report.md). Several
enrichment fields used by hard overrides — sanctions match, terrorist-
financing indicator, "restricted customer" — reference columns that were
**not** part of the profiled/cleaned column set (no SANCTIONS_LISTS join
key or COMPANIES.RESTRICTED_FLAG was confirmed to exist). Those lookups are
wrapped defensively: if the query fails because a column/table isn't there,
the field is recorded as unresolved rather than guessed, and the caller
(scoring engine) treats it as "not triggered" — never fabricated.

CLAUDE.md §26: this adapter has been written to spec but was **not**
exercised against the live tenant in this session (no credentials file was
available in the build sandbox). Run `scripts/init_app_schema.py` once
credentials are in place, then re-run the smoke test
(`scripts/run_demo_checks.py DATA_BACKEND=hana`) before claiming this path
is "implemented and live" rather than "configured but not demonstrated".
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from trustsphere.config.settings import Settings
from trustsphere.domain.alerts import (
    AlertFactorInputs,
    AlertSummary,
    ComplexityBand,
    ComplexityInputs,
    FactorResult,
    HardOverrideResult,
    ScoreResult,
    UrgencyTier,
)
from trustsphere.domain.cases import CaseFile
from trustsphere.domain.citations import Citation
from trustsphere.domain.decisions import (
    ActorType,
    AuditEvent,
    AuditEventType,
    Decision,
    DecisionType,
    WorkflowInstance,
    WorkflowStatus,
)
from trustsphere.persistence.base import Repository

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(obj: Any) -> Any:
    """See local.py's `_jsonable` — same reasoning: a replayed idempotent
    response must serialize identically to the original live response.
    """
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(obj)


class HanaRepository(Repository):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.schema = settings.hana_schema
        self._conn = None  # lazy — see .conn property

    @property
    def conn(self):
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _connect(self):
        from hdbcli import dbapi

        host, port, user, password = (
            self.settings.hana_host,
            self.settings.hana_port,
            self.settings.hana_user,
            self.settings.hana_password,
        )
        if self.settings.team11_creds:
            with open(self.settings.team11_creds) as f:
                db = json.load(f)["database"]
            host, port, user, password = db["host"], db["port"], db["username"], db["password"]
        if not host:
            raise RuntimeError(
                "HANA backend selected but no credentials configured — set TEAM11_CREDS "
                "or HANA_HOST/HANA_USER/HANA_PASSWORD in .env"
            )
        conn = dbapi.connect(
            address=host,
            port=port,
            user=user,
            password=password,
            encrypt=self.settings.hana_encrypt,
            sslValidateCertificate=False,
            # hdbcli's default crypto provider hits "RTE:[1000013] Invalid
            # flags specified" on some Windows TLS stacks; openssl avoids it.
            sslCryptoProvider="openssl",
        )
        conn.setautocommit(True)
        return conn

    def _q(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return rows

    def _x(self, sql: str, params: tuple = ()) -> None:
        cur = self.conn.cursor()
        cur.execute(sql, params)
        cur.close()

    def _t(self, table: str) -> str:
        return f'{self.schema}."{table}"'

    def backend_label(self) -> str:
        return f"hana_cloud:{self.schema}"

    def health_check(self) -> dict[str, Any]:
        try:
            r = self._q(f"SELECT COUNT(*) AS N FROM {self._t('RISK_ALERTS')}")
            return {"backend": self.backend_label(), "ok": True, "alert_count": r[0]["N"]}
        except Exception as e:  # pragma: no cover - depends on live tenant
            return {"backend": self.backend_label(), "ok": False, "error": str(e)[:300]}

    # -- alerts -----------------------------------------------------------
    def list_alerts(
        self, status: str | None = None, limit: int = 200, offset: int = 0
    ) -> list[AlertSummary]:
        if status:
            rows = self._q(
                f"SELECT * FROM {self._t('RISK_ALERTS')} WHERE STATUS = ? "
                f"ORDER BY CREATED_AT ASC LIMIT ? OFFSET ?",
                (status, limit, offset),
            )
        else:
            rows = self._q(
                f"SELECT * FROM {self._t('RISK_ALERTS')} ORDER BY CREATED_AT ASC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [self._row_to_alert_summary(r) for r in rows]

    def get_alert(self, alert_id: str) -> AlertSummary | None:
        rows = self._q(f"SELECT * FROM {self._t('RISK_ALERTS')} WHERE ALERT_ID = ?", (alert_id,))
        return self._row_to_alert_summary(rows[0]) if rows else None

    @staticmethod
    def _row_to_alert_summary(row: dict) -> AlertSummary:
        return AlertSummary(
            alert_id=row["ALERT_ID"],
            company_id=row.get("COMPANY_ID"),
            transaction_id=row.get("TRANSACTION_ID"),
            alert_type=row.get("ALERT_TYPE"),
            alert_priority=row.get("ALERT_PRIORITY"),
            status=row.get("STATUS"),
            created_at=row.get("CREATED_AT"),
            sla_due_at=row.get("SLA_DUE_AT"),
            source_system="RISK_ALERTS",
            dq_flag=row.get("ALERT_DQ_FLAG"),
        )

    def get_alert_factor_inputs(self, alert_id: str, as_of: datetime) -> AlertFactorInputs:
        rows = self._q(
            f"""
            SELECT a.ALERT_ID, a.COMPANY_ID, a.TRANSACTION_ID, a.ALERT_TYPE, a.ALERT_PRIORITY,
                   a.CREATED_AT, a.SLA_DUE_AT,
                   c.KYC_EFFECTIVE_STATUS,
                   crp.COMPOSITE_RISK_SCORE, crp.COUNTRY_RISK_SCORE, crp.RISK_TIER,
                   t.AMOUNT_USD, t.IS_CROSS_BORDER
            FROM {self._t('RISK_ALERTS')} a
            LEFT JOIN {self._t('COMPANIES')} c ON a.COMPANY_ID = c.COMPANY_ID
            LEFT JOIN {self._t('COMPANY_RISK_PROFILES')} crp ON a.COMPANY_ID = crp.COMPANY_ID
            LEFT JOIN {self._t('TRANSACTIONS')} t ON a.TRANSACTION_ID = t.TRANSACTION_ID
            WHERE a.ALERT_ID = ?
            """,
            (alert_id,),
        )
        unresolved: list[str] = []
        if not rows:
            unresolved.append(f"RISK_ALERTS row not found for alert_id={alert_id}")
            return AlertFactorInputs(alert_id=alert_id, unresolved_fields=unresolved)
        row = rows[0]

        baseline_avg = None
        try:
            b = self._q(
                f"SELECT AVG_AMOUNT_USD FROM {self._t('TRANSACTION_BASELINES')} WHERE COMPANY_ID = ? "
                f"ORDER BY PERIOD_START DESC LIMIT 1",
                (row["COMPANY_ID"],),
            )
            if b and b[0]["AVG_AMOUNT_USD"] is not None:
                baseline_avg = Decimal(str(b[0]["AVG_AMOUNT_USD"]))
            else:
                unresolved.append("TRANSACTION_BASELINES not found for company")
        except Exception as e:
            logger.warning("TRANSACTION_BASELINES lookup failed: %s", e)
            unresolved.append("TRANSACTION_BASELINES query failed (schema unverified)")

        sanctions_match = None
        try:
            s = self._q(
                f"SELECT COUNT(*) AS N FROM {self._t('SANCTIONS_LISTS')} WHERE COMPANY_ID = ?",
                (row["COMPANY_ID"],),
            )
            sanctions_match = s[0]["N"] > 0
        except Exception as e:
            logger.warning("SANCTIONS_LISTS lookup failed — treating as not-triggered: %s", e)
            unresolved.append("sanctions_match: SANCTIONS_LISTS.COMPANY_ID join not confirmed against live schema")

        restricted_flag = None
        try:
            rf = self._q(
                f"SELECT RESTRICTED_FLAG FROM {self._t('COMPANIES')} WHERE COMPANY_ID = ?",
                (row["COMPANY_ID"],),
            )
            restricted_flag = bool(rf[0]["RESTRICTED_FLAG"]) if rf and rf[0].get("RESTRICTED_FLAG") is not None else False
        except Exception as e:
            logger.warning("COMPANIES.RESTRICTED_FLAG not available — treating as not-triggered: %s", e)
            unresolved.append("restricted_customer_flag: COMPANIES.RESTRICTED_FLAG column not confirmed against live schema")
            restricted_flag = False

        prior_case_count = 0
        try:
            pc = self._q(
                f"SELECT COUNT(*) AS N FROM {self._t('COMPLIANCE_CASES')} WHERE COMPANY_ID = ?",
                (row["COMPANY_ID"],),
            )
            prior_case_count = pc[0]["N"]
        except Exception as e:
            logger.warning("COMPLIANCE_CASES lookup failed: %s", e)
            unresolved.append("has_prior_escalated_case: COMPLIANCE_CASES query failed")

        created_at, sla_due_at = row.get("CREATED_AT"), row.get("SLA_DUE_AT")
        hours_remaining = None
        if sla_due_at is not None:
            due = sla_due_at if sla_due_at.tzinfo else sla_due_at.replace(tzinfo=timezone.utc)
            hours_remaining = (due - as_of).total_seconds() / 3600.0
        else:
            unresolved.append("SLA_DUE_AT missing")

        return AlertFactorInputs(
            alert_id=alert_id,
            alert_priority=row.get("ALERT_PRIORITY"),
            alert_type=row.get("ALERT_TYPE"),
            composite_risk_score=Decimal(str(row["COMPOSITE_RISK_SCORE"])) if row.get("COMPOSITE_RISK_SCORE") is not None else None,
            kyc_effective_status=row.get("KYC_EFFECTIVE_STATUS"),
            country_risk_score=Decimal(str(row["COUNTRY_RISK_SCORE"])) if row.get("COUNTRY_RISK_SCORE") is not None else None,
            risk_tier=row.get("RISK_TIER"),
            is_cross_border=bool(row["IS_CROSS_BORDER"]) if row.get("IS_CROSS_BORDER") is not None else None,
            created_at=created_at,
            sla_due_at=sla_due_at,
            as_of=as_of,
            hours_remaining_to_sla=hours_remaining,
            amount_usd=Decimal(str(row["AMOUNT_USD"])) if row.get("AMOUNT_USD") is not None else None,
            baseline_avg_amount_usd=baseline_avg,
            sanctions_match=sanctions_match,
            terrorist_financing_flag=(row.get("ALERT_TYPE") == "TERRORIST_FINANCING"),
            restricted_customer_flag=restricted_flag,
            has_prior_escalated_case=prior_case_count > 0,
            unresolved_fields=unresolved,
        )

    def get_complexity_inputs(self, alert_id: str) -> ComplexityInputs:
        rows = self._q(
            f"SELECT COMPANY_ID, TRANSACTION_ID FROM {self._t('RISK_ALERTS')} WHERE ALERT_ID = ?",
            (alert_id,),
        )
        if not rows:
            return ComplexityInputs(alert_id=alert_id)
        company_id, txn_id = rows[0]["COMPANY_ID"], rows[0]["TRANSACTION_ID"]

        entities: set[str] = {company_id} if company_id else set()
        countries: set[str] = set()

        if txn_id:
            t = self._q(
                f"SELECT ORIGINATOR_COMPANY_ID, BENEFICIARY_COMPANY_ID, ORIGINATING_COUNTRY_ID, "
                f"DESTINATION_COUNTRY_ID FROM {self._t('TRANSACTIONS')} WHERE TRANSACTION_ID = ?",
                (txn_id,),
            )
            if t:
                for c in (t[0]["ORIGINATOR_COMPANY_ID"], t[0]["BENEFICIARY_COMPANY_ID"]):
                    if c:
                        entities.add(c)
                for cty in (t[0]["ORIGINATING_COUNTRY_ID"], t[0]["DESTINATION_COUNTRY_ID"]):
                    if cty:
                        countries.add(cty)

        try:
            owners = self._q(
                f"SELECT OWNER_NAME FROM {self._t('COMPANY_BENEFICIAL_OWNERS')} WHERE COMPANY_ID = ?",
                (company_id,),
            )
            entities.update(f"owner:{o['OWNER_NAME']}" for o in owners)
        except Exception as e:
            logger.warning("COMPANY_BENEFICIAL_OWNERS lookup failed: %s", e)

        kyc_status = None
        try:
            kr = self._q(f"SELECT KYC_EFFECTIVE_STATUS FROM {self._t('COMPANIES')} WHERE COMPANY_ID = ?", (company_id,))
            kyc_status = kr[0]["KYC_EFFECTIVE_STATUS"] if kr else None
        except Exception as e:
            logger.warning("COMPANIES.KYC_EFFECTIVE_STATUS lookup failed: %s", e)
        missing_kyc = 1 if kyc_status != "VERIFIED" else 0

        related_alerts = self._q(
            f"SELECT COUNT(*) AS N FROM {self._t('RISK_ALERTS')} WHERE COMPANY_ID = ? AND ALERT_ID != ?",
            (company_id, alert_id),
        )[0]["N"]

        txn_count = self._q(
            f"SELECT COUNT(*) AS N FROM {self._t('TRANSACTIONS')} "
            f"WHERE ORIGINATOR_COMPANY_ID = ? OR BENEFICIARY_COMPANY_ID = ?",
            (company_id, company_id),
        )[0]["N"]
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
        rows = self._q(f"SELECT * FROM {self._t('COMPANIES')} WHERE COMPANY_ID = ?", (company_id,))
        return rows[0] if rows else None

    def get_counterparty_profiles(self, alert_id: str) -> list[dict[str, Any]]:
        rows = self._q(
            f"SELECT COMPANY_ID, TRANSACTION_ID FROM {self._t('RISK_ALERTS')} WHERE ALERT_ID = ?", (alert_id,)
        )
        if not rows or not rows[0].get("TRANSACTION_ID"):
            return []
        this_company, txn_id = rows[0]["COMPANY_ID"], rows[0]["TRANSACTION_ID"]
        t = self._q(
            f"SELECT BENEFICIARY_NAME, BENEFICIARY_COMPANY_ID, BENEFICIARY_COUNTRY_ID, "
            f"ORIGINATOR_COMPANY_ID FROM {self._t('TRANSACTIONS')} WHERE TRANSACTION_ID = ?",
            (txn_id,),
        )
        if not t:
            return []
        t = t[0]
        results = []
        if t.get("BENEFICIARY_COMPANY_ID") and t["BENEFICIARY_COMPANY_ID"] != this_company:
            n = self._q(
                f"SELECT COUNT(*) AS N FROM {self._t('TRANSACTIONS')} "
                f"WHERE BENEFICIARY_COMPANY_ID = ? OR ORIGINATOR_COMPANY_ID = ?",
                (t["BENEFICIARY_COMPANY_ID"], t["BENEFICIARY_COMPANY_ID"]),
            )[0]["N"]
            results.append({
                "counterparty_label": t.get("BENEFICIARY_NAME") or t["BENEFICIARY_COMPANY_ID"],
                "jurisdiction_country_id": t.get("BENEFICIARY_COUNTRY_ID"),
                "appearance_count": n,
            })
        return results

    def get_transaction_timeline(self, company_id: str, limit: int = 25) -> list[dict[str, Any]]:
        return self._q(
            f"SELECT * FROM {self._t('TRANSACTIONS')} WHERE ORIGINATOR_COMPANY_ID = ? OR BENEFICIARY_COMPANY_ID = ? "
            f"ORDER BY INITIATED_AT DESC LIMIT ?",
            (company_id, company_id, limit),
        )

    def get_related_alerts(self, alert_id: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._q(f"SELECT COMPANY_ID FROM {self._t('RISK_ALERTS')} WHERE ALERT_ID = ?", (alert_id,))
        if not rows:
            return []
        return self._q(
            f"SELECT ALERT_ID, ALERT_TYPE, STATUS, COMPANY_ID FROM {self._t('RISK_ALERTS')} "
            f"WHERE COMPANY_ID = ? AND ALERT_ID != ? ORDER BY CREATED_AT DESC LIMIT ?",
            (rows[0]["COMPANY_ID"], alert_id, limit),
        )

    def get_beneficial_owners(self, company_id: str) -> list[dict[str, Any]]:
        try:
            return self._q(f"SELECT * FROM {self._t('COMPANY_BENEFICIAL_OWNERS')} WHERE COMPANY_ID = ?", (company_id,))
        except Exception as e:
            logger.warning("COMPANY_BENEFICIAL_OWNERS lookup failed: %s", e)
            return []

    def list_policy_passages(self) -> list[dict[str, Any]]:
        try:
            return self._q(f"SELECT DOCUMENT_ID, PASSAGE_LOCATOR, TEXT_CONTENT AS TEXT, DOC_TYPE, REGION "
                            f"FROM {self._t('POLICY_PASSAGES')}")
        except Exception as e:
            logger.warning("POLICY_PASSAGES read failed (has scripts/load_policy_corpus.py been run?): %s", e)
            return []

    def search_policy_passages_native(self, query_text: str, limit: int = 5) -> list[dict[str, Any]]:
        """HANA-native path: in-DB VECTOR_EMBEDDING + COSINE_SIMILARITY
        (both verified capabilities — docs/capability-matrix.md). Requires
        POLICY_PASSAGES.EMBEDDING to already be populated by
        scripts/load_policy_corpus.py.
        """
        return self._q(
            f"""SELECT DOCUMENT_ID, PASSAGE_LOCATOR, TEXT_CONTENT AS TEXT, DOC_TYPE, REGION,
                       COSINE_SIMILARITY(EMBEDDING, VECTOR_EMBEDDING(?, 'QUERY', 'SAP_NEB.20240715')) AS SIMILARITY
                FROM {self._t('POLICY_PASSAGES')}
                ORDER BY SIMILARITY DESC
                LIMIT ?""",
            (query_text, limit),
        )

    def list_sla_training_alert_ids(self) -> list[tuple[str, float]]:
        rows = self._q(
            f"""SELECT ALERT_ID, RESOLUTION_HOURS FROM {self._t('RISK_ALERTS')}
                WHERE STATUS IN ('CLOSED_TRUE', 'CLOSED_FALSE')
                  AND RESOLUTION_HOURS IS NOT NULL
                  AND (ALERT_DQ_FLAG IS NULL OR ALERT_DQ_FLAG != 'RESOLVED_BEFORE_CREATED')"""
        )
        return [(r["ALERT_ID"], float(r["RESOLUTION_HOURS"])) for r in rows]

    # -- scoring ------------------------------------------------------------
    def save_score(self, score: ScoreResult) -> None:
        self._x(f"DELETE FROM {self._t('PRIORITY_SCORES')} WHERE ALERT_ID = ?", (score.alert_id,))
        self._x(
            f"""INSERT INTO {self._t('PRIORITY_SCORES')}
                (ALERT_ID, URGENCY_SCORE, URGENCY_TIER, HARD_OVERRIDE_CODE, COMPLEXITY_BAND,
                 COMPLEXITY_POINTS, POLICY_VERSION, CALCULATED_AT, CAVEATS_JSON)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                score.alert_id, score.urgency_score, score.urgency_tier.value,
                score.hard_override.code if score.hard_override else None,
                score.complexity_band.value, score.complexity_points, score.policy_version,
                score.calculated_at, json.dumps(score.caveats),
            ),
        )
        self._x(f"DELETE FROM {self._t('ALERT_FACTORS')} WHERE ALERT_ID = ?", (score.alert_id,))
        for f in score.factors:
            self._x(
                f"""INSERT INTO {self._t('ALERT_FACTORS')}
                    (ALERT_ID, FACTOR_CODE, RAW_VALUE, NORMALISED_VALUE, WEIGHT, WEIGHTED_POINTS,
                     REASON_CODE, POLICY_VERSION, CALCULATED_AT)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    score.alert_id, f.factor_code, f.raw_value, f.normalised_value, f.weight,
                    f.weighted_points, f.reason_code, f.policy_version, score.calculated_at,
                ),
            )

    def get_latest_score(self, alert_id: str) -> ScoreResult | None:
        rows = self._q(f"SELECT * FROM {self._t('PRIORITY_SCORES')} WHERE ALERT_ID = ?", (alert_id,))
        if not rows:
            return None
        row = rows[0]
        factor_rows = self._q(f"SELECT * FROM {self._t('ALERT_FACTORS')} WHERE ALERT_ID = ?", (alert_id,))
        factors = [
            FactorResult(
                factor_code=fr["FACTOR_CODE"], raw_value=fr.get("RAW_VALUE"),
                normalised_value=fr["NORMALISED_VALUE"], weight=fr["WEIGHT"],
                weighted_points=fr["WEIGHTED_POINTS"], reason_code=fr["REASON_CODE"],
                policy_version=fr["POLICY_VERSION"],
            )
            for fr in factor_rows
        ]
        hard_override = None
        if row.get("HARD_OVERRIDE_CODE"):
            hard_override = HardOverrideResult(
                code=row["HARD_OVERRIDE_CODE"], forced_tier=UrgencyTier(row["URGENCY_TIER"]),
                reason=row["HARD_OVERRIDE_CODE"],
            )
        return ScoreResult(
            alert_id=alert_id, urgency_score=float(row["URGENCY_SCORE"]),
            urgency_tier=UrgencyTier(row["URGENCY_TIER"]), hard_override=hard_override,
            factors=factors, complexity_band=ComplexityBand(row["COMPLEXITY_BAND"]),
            complexity_points=row["COMPLEXITY_POINTS"], policy_version=row["POLICY_VERSION"],
            calculated_at=row["CALCULATED_AT"],
            caveats=json.loads(row["CAVEATS_JSON"]) if row.get("CAVEATS_JSON") else [],
        )

    def count_scored_open_alerts(self) -> int:
        rows = self._q(
            f"""SELECT COUNT(*) AS N FROM {self._t('RISK_ALERTS')} a
                JOIN {self._t('PRIORITY_SCORES')} p ON a.ALERT_ID = p.ALERT_ID
                WHERE a.STATUS NOT LIKE 'CLOSED%'""")
        return int(rows[0]["N"])

    def list_scored_alerts_ordered(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        return self._q(
            f"""
            SELECT a.ALERT_ID, a.ALERT_TYPE, a.STATUS, a.SLA_DUE_AT,
                   p.URGENCY_SCORE, p.URGENCY_TIER, p.HARD_OVERRIDE_CODE,
                   p.COMPLEXITY_BAND, p.COMPLEXITY_POINTS, p.CALCULATED_AT,
                   c.CASE_ID, c.STATUS AS CASE_STATUS
            FROM {self._t('RISK_ALERTS')} a
            JOIN {self._t('PRIORITY_SCORES')} p ON a.ALERT_ID = p.ALERT_ID
            LEFT JOIN {self._t('CASES')} c ON c.ALERT_ID = a.ALERT_ID
            WHERE a.STATUS NOT LIKE 'CLOSED%'
            ORDER BY
                CASE WHEN p.HARD_OVERRIDE_CODE IS NOT NULL THEN 0 ELSE 1 END ASC,
                CASE p.URGENCY_TIER
                    WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3
                END ASC,
                a.SLA_DUE_AT ASC,
                p.URGENCY_SCORE DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )

    # -- predictive SLA -----------------------------------------------------
    def save_predictive_score(
        self, alert_id: str, prediction_type: str, prediction_value: float, model_name: str,
        model_version: str, feature_snapshot_id: str, scored_at: datetime, extra: dict | None = None,
    ) -> None:
        self._x(
            f"""INSERT INTO {self._t('PREDICTIVE_SCORES')}
                (ALERT_ID, PREDICTION_TYPE, PREDICTION_VALUE, MODEL_NAME, MODEL_VERSION,
                 FEATURE_SNAPSHOT_ID, ADVISORY_ONLY, SCORED_AT, EXTRA_JSON)
                VALUES (?, ?, ?, ?, ?, ?, TRUE, ?, ?)""",
            (alert_id, prediction_type, prediction_value, model_name, model_version,
             feature_snapshot_id, scored_at, json.dumps(extra or {})),
        )

    def get_latest_predictive_score(self, alert_id: str) -> dict[str, Any] | None:
        rows = self._q(
            f"SELECT * FROM {self._t('PREDICTIVE_SCORES')} WHERE ALERT_ID = ? ORDER BY SCORED_AT DESC LIMIT 1",
            (alert_id,),
        )
        if not rows:
            return None
        row = rows[0]
        row["EXTRA_JSON"] = json.loads(row["EXTRA_JSON"]) if row.get("EXTRA_JSON") else {}
        return row

    # -- cases / case files ---------------------------------------------------
    def get_or_create_case(self, alert_id: str, assigned_team: str, region: str) -> str:
        rows = self._q(f"SELECT CASE_ID FROM {self._t('CASES')} WHERE ALERT_ID = ?", (alert_id,))
        if rows:
            return rows[0]["CASE_ID"]
        case_id = f"CASE-{alert_id}"
        now = _now()
        self._x(
            f"""INSERT INTO {self._t('CASES')}
                (CASE_ID, ALERT_ID, ASSIGNED_TEAM, STATUS, CREATED_AT, UPDATED_AT, REGION)
                VALUES (?, ?, ?, 'OPEN', ?, ?, ?)""",
            (case_id, alert_id, assigned_team, now, now, region),
        )
        return case_id

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        rows = self._q(f"SELECT * FROM {self._t('CASES')} WHERE CASE_ID = ?", (case_id,))
        return rows[0] if rows else None

    def update_case_status(self, case_id: str, status: str) -> None:
        self._x(
            f"UPDATE {self._t('CASES')} SET STATUS = ?, UPDATED_AT = ? WHERE CASE_ID = ?",
            (status, datetime.now(timezone.utc), case_id),
        )

    def save_case_file(self, case_file: CaseFile) -> None:
        self._x(
            f"""INSERT INTO {self._t('CASE_FILES')}
                (CASE_FILE_ID, CASE_ID, SCHEMA_VERSION, CONTENT_JSON, ASSEMBLED_AT, SOURCE_COVERAGE)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (case_file.case_file_id, case_file.case_id, case_file.schema_version,
             case_file.model_dump_json(), case_file.assembled_at, case_file.source_coverage),
        )

    def get_latest_case_file(self, case_id: str) -> CaseFile | None:
        rows = self._q(
            f"SELECT * FROM {self._t('CASE_FILES')} WHERE CASE_ID = ? ORDER BY ASSEMBLED_AT DESC LIMIT 1",
            (case_id,),
        )
        if not rows:
            return None
        return CaseFile.model_validate_json(rows[0]["CONTENT_JSON"])

    def save_citations(self, case_file_id: str, citations: list[Citation]) -> None:
        for c in citations:
            self._x(
                f"""INSERT INTO {self._t('SOURCE_CITATIONS')}
                    (CITATION_ID, CASE_FILE_ID, SOURCE_TYPE, SOURCE_ID, SOURCE_LOCATOR,
                     SOURCE_VERSION, RETRIEVED_AT, REGION, PERMISSION_SCOPE)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (c.citation_id, case_file_id, c.source_type.value, c.source_id, c.source_locator,
                 c.source_version, c.retrieved_at, c.region, c.permission_scope),
            )

    # -- narrative drafts -----------------------------------------------------
    def save_draft(
        self, case_id: str, content: str, generation_id: str, prompt_version: str,
        model_version: str, created_by_type: str, verification_status: str = "unverified",
    ) -> dict[str, Any]:
        prev = self._q(
            f"SELECT MAX(DRAFT_VERSION) AS MX FROM {self._t('NARRATIVE_DRAFTS')} WHERE CASE_ID = ?",
            (case_id,),
        )
        next_version = (prev[0]["MX"] or 0) + 1 if prev else 1
        draft_id = f"DRAFT-{case_id}-{next_version}"
        now = _now()
        self._x(
            f"""INSERT INTO {self._t('NARRATIVE_DRAFTS')}
                (DRAFT_ID, CASE_ID, DRAFT_VERSION, CONTENT, GENERATION_ID, PROMPT_VERSION,
                 MODEL_VERSION, CREATED_BY_TYPE, CREATED_AT, VERIFICATION_STATUS)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (draft_id, case_id, next_version, content, generation_id, prompt_version,
             model_version, created_by_type, now, verification_status),
        )
        return self.get_latest_draft(case_id)

    def get_latest_draft(self, case_id: str) -> dict[str, Any] | None:
        rows = self._q(
            f"SELECT * FROM {self._t('NARRATIVE_DRAFTS')} WHERE CASE_ID = ? ORDER BY DRAFT_VERSION DESC LIMIT 1",
            (case_id,),
        )
        return rows[0] if rows else None

    # -- decisions / workflow / audit -----------------------------------------
    def save_decision(self, decision: Decision) -> None:
        self._x(
            f"""INSERT INTO {self._t('DECISIONS')}
                (DECISION_ID, CASE_ID, DECISION_TYPE, RATIONALE, DECIDED_BY, ATTESTED, DECIDED_AT)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (decision.decision_id, decision.case_id, decision.decision_type.value,
             decision.rationale, decision.decided_by, decision.attested, decision.decided_at),
        )

    def list_decisions(self, case_id: str) -> list[Decision]:
        rows = self._q(f"SELECT * FROM {self._t('DECISIONS')} WHERE CASE_ID = ? ORDER BY DECIDED_AT ASC", (case_id,))
        return [
            Decision(
                decision_id=r["DECISION_ID"], case_id=r["CASE_ID"],
                decision_type=DecisionType(r["DECISION_TYPE"]), rationale=r["RATIONALE"],
                decided_by=r["DECIDED_BY"], attested=bool(r["ATTESTED"]), decided_at=r["DECIDED_AT"],
            )
            for r in rows
        ]

    def save_workflow_instance(self, workflow: WorkflowInstance) -> None:
        existing = self._q(
            f"SELECT WORKFLOW_ID FROM {self._t('WORKFLOW_INSTANCES')} WHERE WORKFLOW_ID = ?",
            (workflow.workflow_id,),
        )
        if existing:
            self._x(
                f"""UPDATE {self._t('WORKFLOW_INSTANCES')}
                    SET STATUS = ?, COMPLETED_AT = ?, EXTERNAL_INSTANCE_ID = ?, IS_FALLBACK = ?
                    WHERE WORKFLOW_ID = ?""",
                (
                    workflow.status.value, workflow.completed_at, workflow.external_instance_id,
                    workflow.is_fallback, workflow.workflow_id,
                ),
            )
        else:
            self._x(
                f"""INSERT INTO {self._t('WORKFLOW_INSTANCES')}
                    (WORKFLOW_ID, CASE_ID, EXTERNAL_INSTANCE_ID, STATUS, STARTED_AT, COMPLETED_AT, IS_FALLBACK)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (workflow.workflow_id, workflow.case_id, workflow.external_instance_id,
                 workflow.status.value, workflow.started_at, workflow.completed_at, workflow.is_fallback),
            )

    def get_latest_workflow_instance(self, case_id: str) -> WorkflowInstance | None:
        rows = self._q(
            f"SELECT * FROM {self._t('WORKFLOW_INSTANCES')} WHERE CASE_ID = ? ORDER BY STARTED_AT DESC LIMIT 1",
            (case_id,),
        )
        if not rows:
            return None
        r = rows[0]
        return WorkflowInstance(
            workflow_id=r["WORKFLOW_ID"], case_id=r["CASE_ID"],
            external_instance_id=r.get("EXTERNAL_INSTANCE_ID"), status=WorkflowStatus(r["STATUS"]),
            started_at=r["STARTED_AT"], completed_at=r.get("COMPLETED_AT"),
            is_fallback=bool(r["IS_FALLBACK"]),
        )

    def append_audit_event(self, event: AuditEvent) -> None:
        self._x(
            f"""INSERT INTO {self._t('AUDIT_EVENTS')}
                (EVENT_ID, CASE_ID, EVENT_TYPE, ACTOR_TYPE, ACTOR_ID, OBJECT_TYPE, OBJECT_ID,
                 DETAILS_JSON, OCCURRED_AT, CORRELATION_ID)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event.event_id, event.case_id, event.event_type.value, event.actor_type.value,
             event.actor_id, event.object_type, event.object_id, json.dumps(event.details),
             event.occurred_at, event.correlation_id),
        )

    def list_audit_events(self, case_id: str) -> list[AuditEvent]:
        rows = self._q(f"SELECT * FROM {self._t('AUDIT_EVENTS')} WHERE CASE_ID = ? ORDER BY OCCURRED_AT ASC", (case_id,))
        return [
            AuditEvent(
                event_id=r["EVENT_ID"], case_id=r["CASE_ID"], event_type=AuditEventType(r["EVENT_TYPE"]),
                actor_type=ActorType(r["ACTOR_TYPE"]), actor_id=r["ACTOR_ID"], object_type=r["OBJECT_TYPE"],
                object_id=r["OBJECT_ID"], details=json.loads(r["DETAILS_JSON"]),
                occurred_at=r["OCCURRED_AT"], correlation_id=r["CORRELATION_ID"],
            )
            for r in rows
        ]

    # -- idempotency ----------------------------------------------------------
    def check_and_store_idempotency_key(self, key: str, endpoint: str, request_hash: str) -> dict[str, Any] | None:
        rows = self._q(
            f"SELECT REQUEST_HASH, RESPONSE_JSON FROM {self._t('IDEMPOTENCY_KEYS')} "
            f"WHERE IDEMPOTENCY_KEY = ? AND ENDPOINT = ?",
            (key, endpoint),
        )
        if not rows:
            return None
        if rows[0]["REQUEST_HASH"] != request_hash:
            raise ValueError(f"Idempotency key {key!r} reused with a different request body on {endpoint}")
        return json.loads(rows[0]["RESPONSE_JSON"])

    def store_idempotent_response(self, key: str, endpoint: str, request_hash: str, response: dict[str, Any]) -> None:
        self._x(
            f"""INSERT INTO {self._t('IDEMPOTENCY_KEYS')}
                (IDEMPOTENCY_KEY, ENDPOINT, REQUEST_HASH, RESPONSE_JSON, CREATED_AT)
                VALUES (?, ?, ?, ?, ?)""",
            (key, endpoint, request_hash, json.dumps(_jsonable(response)), _now()),
        )
