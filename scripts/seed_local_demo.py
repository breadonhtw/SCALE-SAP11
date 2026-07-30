#!/usr/bin/env python
"""Seed the local SQLite fallback with a small, clearly-synthetic demo set.

Only used when DATA_BACKEND=local (CLAUDE.md §18 "local prototype fallback")
— i.e. running/demoing Track A without the team-11 HANA credentials file.
Against the real tenant, DATA_BACKEND=hana and this script is not used; the
cleaned TEAM_11_USER snapshot (scripts/clean_data.py) is the data of record.

Shape mirrors CLAUDE.md §21 "Demo flow": one hero case, at least two other
alert types, one clean/held-out case that retrieval should NOT flag, and a
handful of closed historical alerts so the SLA advisory model (A4) has
something to train on.

Idempotent: truncates and reloads every table each run, so it is safe to
re-run after schema or fixture changes.

Usage:
    python scripts/seed_local_demo.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trustsphere.config import get_settings  # noqa: E402
from trustsphere.persistence.local import LocalSQLiteRepository  # noqa: E402

NOW = datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

COUNTRIES = [
    ("SGP", "Singapore", "LOW"),
    ("USA", "United States", "LOW"),
    ("GBR", "United Kingdom", "LOW"),
    ("KYM", "Cayman Islands", "HIGH"),
    ("PAN", "Panama", "HIGH"),
]

COMPANIES = [
    # company_id, legal_name, reg_no, lei, incorp_country, hq_country, kyc_status,
    # kyc_risk_rating, kyc_effective_status, client_segment, restricted_flag
    ("CMP-1001", "Meridian Trading Pte Ltd", "SG-REG-100234", "5493001KJTIIGC8Y1R12",
     "KYM", "SGP", "EXPIRED", "HIGH", "EXPIRED", "CORPORATE", 0),
    ("CMP-1002", "Straits Logistics Pte Ltd", "SG-REG-100987", "5493001KJTIIGC8Y1R45",
     "SGP", "SGP", "VERIFIED", "MEDIUM", "VERIFIED", "CORPORATE", 0),
    ("CMP-1003", "Alpine Resources Ltd", "KY-REG-882301", "5493001KJTIIGC8Y1R99",
     "KYM", "PAN", "REJECTED", "CRITICAL", "REJECTED", "TRUST", 1),
    ("CMP-1004", "Northgate Holdings Pte Ltd", "SG-REG-101550", "5493001KJTIIGC8Y1R33",
     "SGP", "SGP", "VERIFIED", "LOW", "VERIFIED", "CORPORATE", 0),
    ("CMP-2001", "Silverlake Ventures Ltd", "KY-REG-990120", None,
     "KYM", "KYM", "PENDING", "HIGH", "PENDING", "TRUST", 0),
]

RISK_PROFILES = [
    # company_id, composite, country, risk_tier
    ("CMP-1001", 88.0, 82.0, "HIGH"),
    ("CMP-1002", 45.0, 30.0, "MEDIUM"),
    ("CMP-1003", 95.0, 90.0, "CRITICAL"),
    ("CMP-1004", 20.0, 10.0, "LOW"),
    ("CMP-2001", 70.0, 78.0, "HIGH"),
]

BENEFICIAL_OWNERS = [
    # company_id, owner_name, pct, pep_flag, sanctions_ref_flag
    ("CMP-1001", "Viktor Kessler", 60.0, 1, 0),
    ("CMP-1003", "Viktor Kessler", 45.0, 1, 0),  # shared BO across two companies
    ("CMP-1002", "Amara Chen", 100.0, 0, 0),
    ("CMP-1004", "Daniel Ho", 100.0, 0, 0),
]

# transaction_id, originator, beneficiary_company, beneficiary_name, amount_usd,
# currency, initiated_at(offset days), origin_country, dest_country, cross_border
TRANSACTIONS = [
    ("TXN-5001", "CMP-1001", "CMP-2001", "Silverlake Ventures Ltd", 850000.0,
     "USD", -1, "SGP", "KYM", 1),
    ("TXN-5002", "CMP-1001", None, "Cayman Nominee Services", 60000.0,
     "USD", -8, "SGP", "KYM", 1),
    ("TXN-5003", "CMP-1001", None, "Cayman Nominee Services", 72000.0,
     "USD", -15, "SGP", "KYM", 1),
    ("TXN-6001", "CMP-1002", "CMP-1004", "Northgate Holdings Pte Ltd", 22000.0,
     "SGD", -2, "SGP", "SGP", 0),
    ("TXN-6002", "CMP-1002", None, "Straits Freight Co", 18500.0,
     "SGD", -9, "SGP", "SGP", 0),
    ("TXN-7001", "CMP-1003", "CMP-2001", "Silverlake Ventures Ltd", 500000.0,
     "USD", -3, "PAN", "KYM", 1),
    ("TXN-7002", "CMP-1003", None, "Panama Trading Corp", 48000.0,
     "USD", -20, "PAN", "PAN", 0),
    ("TXN-8001", "CMP-1004", "CMP-1002", "Straits Logistics Pte Ltd", 15200.0,
     "SGD", -4, "SGP", "SGP", 0),
    ("TXN-8002", "CMP-1004", None, "Local Supplies Pte Ltd", 13800.0,
     "SGD", -11, "SGP", "SGP", 0),
]

# company_id, avg_amount_usd
BASELINES = [
    ("CMP-1001", 95000.0),
    ("CMP-1002", 17000.0),
    ("CMP-1003", 49000.0),
    ("CMP-1004", 14500.0),
]

# alert_id, company_id, transaction_id, alert_type, priority, status,
# created_at(hours offset), sla_due_at(hours offset)
ALERTS = [
    # Hero: sanctions hit -> hard override CRITICAL, plus imminent SLA.
    ("ALERT-9001", "CMP-1001", "TXN-5001", "SANCTIONS_SCREENING_HIT", "CRITICAL",
     "OPEN", -20, 4),
    # Contrasting: repeat-escalated customer -> hard override HIGH.
    ("ALERT-9002", "CMP-1002", "TXN-6001", "UNUSUAL_TRANSACTION_VELOCITY", "MEDIUM",
     "OPEN", -10, 60),
    # Contrasting: restricted/exited customer -> hard override CRITICAL, different typology.
    ("ALERT-9003", "CMP-1003", "TXN-7001", "LARGE_CASH_STRUCTURING", "HIGH",
     "OPEN", -5, 90),
    # Held-out / regression fixture: clean company, no overrides, low urgency —
    # proves retrieval and scoring are not hardcoded to the hero case.
    ("ALERT-9004", "CMP-1004", "TXN-8001", "PEP_ASSOCIATION_REVIEW", "LOW",
     "OPEN", -2, 300),
]

# Closed historical alerts for A4 SLA-advisory training (CLAUDE.md §0: exclude
# ALERT_DQ_FLAG='RESOLVED_BEFORE_CREATED' rows from duration training).
HISTORICAL_ALERTS = [
    # alert_id, company_id, alert_type, priority, status, created_offset_days,
    # sla_offset_days, resolved_offset_days, resolution_hours, dq_flag
    ("ALERT-8801", "CMP-1002", "UNUSUAL_TRANSACTION_VELOCITY", "MEDIUM", "CLOSED_TRUE", -30, -27, -28, 48.0, None),
    ("ALERT-8802", "CMP-1002", "STRUCTURING_PATTERN", "HIGH", "CLOSED_TRUE", -45, -42, -43, 36.0, None),
    ("ALERT-8803", "CMP-1004", "PEP_ASSOCIATION_REVIEW", "LOW", "CLOSED_FALSE", -60, -57, -59, 20.0, None),
    ("ALERT-8804", "CMP-1004", "UNUSUAL_TRANSACTION_VELOCITY", "MEDIUM", "CLOSED_TRUE", -20, -17, -18, 30.0, None),
    ("ALERT-8805", "CMP-1001", "SANCTIONS_SCREENING_HIT", "CRITICAL", "CLOSED_TRUE", -90, -87, -89, 12.0, None),
    ("ALERT-8806", "CMP-1003", "LARGE_CASH_STRUCTURING", "HIGH", "CLOSED_TRUE", -75, -72, -73, 44.0, None),
    # A data-quality edge case: resolved timestamp before created — excluded from
    # duration training via ALERT_DQ_FLAG, matching docs/data-quality-report.md.
    ("ALERT-8807", "CMP-1002", "UNUSUAL_TRANSACTION_VELOCITY", "MEDIUM", "CLOSED_TRUE", -10, -7, -11, 2.0, "RESOLVED_BEFORE_CREATED"),
]

SANCTIONS_LISTS = [
    ("Meridian Trading Pte Ltd", "OFAC SDN", "CMP-1001"),
]

# A prior compliance case on CMP-1002 -> REPEAT_ESCALATED_ALERT hard override.
COMPLIANCE_CASES = [
    ("CASE-HIST-001", "CMP-1002", -200, -180, "CLOSED", 0, None),
]

POLICY_PASSAGES = [
    # document_id, passage_locator, text (title folded in), doc_type, region, effective_date
    ("POL-AML-001", "SEC-3.2",
     "Sanctions Screening Escalation. Any confirmed match against an active sanctions list "
     "requires immediate escalation to senior compliance review and forced CRITICAL "
     "prioritisation, regardless of the alert's computed urgency score. Escalation must occur "
     "within 4 working hours of match confirmation.",
     "sanctions_policy", "APJ", "2025-01-01"),
    ("POL-AML-002", "SEC-5.1",
     "Missing Information Handling. Where a required data element (KYC status, beneficial "
     "ownership, transaction baseline) cannot be retrieved, the investigator must record the gap "
     "explicitly in the case file and may not infer or estimate the missing value. Cases with "
     "unresolved critical fields should be flagged for supervisory review before closure.",
     "procedure", "APJ", "2025-01-01"),
    ("POL-AML-003", "SEC-2.4",
     "Structuring and Large-Cash Monitoring Intent. The structuring monitoring rule is intended "
     "to detect patterns of transactions kept below reporting thresholds, or single large "
     "transactions materially inconsistent with a customer's established baseline. A ratio above "
     "8x the customer's average transaction size is treated as high materiality.",
     "rule_intent", "APJ", "2025-01-01"),
    ("POL-AML-004", "SEC-6.3",
     "SLA and Ageing Safeguards. Alerts within 24 hours of their SLA due date must be treated as "
     "near-breach and prioritised accordingly. A small sample of low-ranked alerts must be "
     "periodically pulled into review to prevent indefinite ageing in the queue.",
     "sla_policy", "APJ", "2025-01-01"),
    ("POL-AML-005", "SEC-4.7",
     "High-Risk Jurisdiction Corridors. Transactions crossing into or out of jurisdictions rated "
     "HIGH or CRITICAL risk tier, particularly involving Cayman Islands or Panama intermediary "
     "entities, require documented review of beneficial ownership and counterparty purpose before "
     "an alert may be closed.",
     "jurisdiction_policy", "APJ", "2025-01-01"),
]


def main() -> None:
    settings = get_settings()
    repo = LocalSQLiteRepository(settings.local_db_path)
    conn = repo.conn

    print(f"Seeding local demo data into {settings.local_db_path}")

    with conn:
        # Wipe everything — this script owns the full local dataset.
        for table in [
            "IDEMPOTENCY_KEYS", "AUDIT_EVENTS", "WORKFLOW_INSTANCES", "DECISIONS",
            "NARRATIVE_DRAFTS", "SOURCE_CITATIONS", "CASE_FILES", "PREDICTIVE_SCORES",
            "ALERT_FACTORS", "PRIORITY_SCORES", "CASES", "CASE_ALERTS", "COMPLIANCE_CASES",
            "SANCTIONS_LISTS", "RISK_ALERTS", "TRANSACTION_BASELINES", "TRANSACTIONS",
            "COMPANY_BENEFICIAL_OWNERS", "COMPANY_RISK_PROFILES", "POLICY_PASSAGES",
            "COMPANIES", "COUNTRIES", "DQ_ISSUES",
        ]:
            conn.execute(f"DELETE FROM {table}")

        conn.executemany(
            "INSERT INTO COUNTRIES (COUNTRY_ID, COUNTRY_NAME, RISK_RATING) VALUES (?, ?, ?)",
            COUNTRIES,
        )

        conn.executemany(
            """INSERT INTO COMPANIES
               (COMPANY_ID, LEGAL_NAME, REGISTRATION_NUMBER, LEI_CODE, INCORPORATION_COUNTRY_ID,
                HEADQUARTERS_COUNTRY_ID, KYC_STATUS, KYC_RISK_RATING, KYC_EFFECTIVE_STATUS,
                CLIENT_SEGMENT, RESTRICTED_FLAG)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            COMPANIES,
        )

        conn.executemany(
            """INSERT INTO COMPANY_RISK_PROFILES
               (COMPANY_ID, COMPOSITE_RISK_SCORE, COUNTRY_RISK_SCORE, RISK_TIER)
               VALUES (?, ?, ?, ?)""",
            RISK_PROFILES,
        )

        conn.executemany(
            """INSERT INTO COMPANY_BENEFICIAL_OWNERS
               (COMPANY_ID, OWNER_NAME, OWNERSHIP_PERCENTAGE, PEP_FLAG, SANCTIONS_REFERENCE_FLAG)
               VALUES (?, ?, ?, ?, ?)""",
            BENEFICIAL_OWNERS,
        )

        for txn_id, orig, benef_co, benef_name, amount, ccy, day_off, origin_cty, dest_cty, cross in TRANSACTIONS:
            initiated = NOW + timedelta(days=day_off)
            conn.execute(
                """INSERT INTO TRANSACTIONS
                   (TRANSACTION_ID, ORIGINATOR_COMPANY_ID, BENEFICIARY_COMPANY_ID, BENEFICIARY_NAME,
                    AMOUNT_ORIGINAL, AMOUNT_USD, CURRENCY_ORIGINAL, INITIATED_AT, STATUS,
                    ORIGINATING_COUNTRY_ID, DESTINATION_COUNTRY_ID, BENEFICIARY_COUNTRY_ID,
                    TRANSACTION_TYPE, IS_CROSS_BORDER)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SETTLED', ?, ?, ?, 'WIRE', ?)""",
                (txn_id, orig, benef_co, benef_name, amount, amount, ccy, iso(initiated),
                 origin_cty, dest_cty, dest_cty, cross),
            )

        for company_id, avg_amount in BASELINES:
            conn.execute(
                """INSERT INTO TRANSACTION_BASELINES
                   (COMPANY_ID, BASELINE_PERIOD, PERIOD_START, PERIOD_END, AVG_AMOUNT_USD)
                   VALUES (?, 'TRAILING_90D', ?, ?, ?)""",
                (company_id, iso(NOW - timedelta(days=90)), iso(NOW), avg_amount),
            )

        for alert_id, company_id, txn_id, alert_type, priority, status, created_off_h, sla_off_h in ALERTS:
            created_at = NOW + timedelta(hours=created_off_h)
            sla_due_at = NOW + timedelta(hours=sla_off_h)
            conn.execute(
                """INSERT INTO RISK_ALERTS
                   (ALERT_ID, COMPANY_ID, TRANSACTION_ID, ALERT_TYPE, ALERT_PRIORITY, STATUS,
                    CREATED_AT, SLA_DUE_AT)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (alert_id, company_id, txn_id, alert_type, priority, status,
                 iso(created_at), iso(sla_due_at)),
            )

        for (alert_id, company_id, alert_type, priority, status, created_off_d, sla_off_d,
             resolved_off_d, resolution_hours, dq_flag) in HISTORICAL_ALERTS:
            created_at = NOW + timedelta(days=created_off_d)
            sla_due_at = NOW + timedelta(days=sla_off_d)
            resolved_at = NOW + timedelta(days=resolved_off_d)
            conn.execute(
                """INSERT INTO RISK_ALERTS
                   (ALERT_ID, COMPANY_ID, ALERT_TYPE, ALERT_PRIORITY, STATUS, CREATED_AT,
                    SLA_DUE_AT, RESOLVED_AT, RESOLUTION_HOURS, ALERT_DQ_FLAG)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (alert_id, company_id, alert_type, priority, status, iso(created_at),
                 iso(sla_due_at), iso(resolved_at), resolution_hours, dq_flag),
            )

        conn.executemany(
            "INSERT INTO SANCTIONS_LISTS (ENTITY_NAME, LIST_NAME, COMPANY_ID) VALUES (?, ?, ?)",
            SANCTIONS_LISTS,
        )

        for case_id, company_id, opened_off_d, closed_off_d, status, sar_filed, sar_ref in COMPLIANCE_CASES:
            conn.execute(
                """INSERT INTO COMPLIANCE_CASES
                   (CASE_ID, COMPANY_ID, OPENED_AT, CLOSED_AT, STATUS, SAR_FILED, SAR_REFERENCE)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (case_id, company_id, iso(NOW + timedelta(days=opened_off_d)),
                 iso(NOW + timedelta(days=closed_off_d)), status, sar_filed, sar_ref),
            )

        conn.executemany(
            """INSERT INTO POLICY_PASSAGES
               (DOCUMENT_ID, PASSAGE_LOCATOR, TEXT, DOC_TYPE, REGION, EFFECTIVE_DATE)
               VALUES (?, ?, ?, ?, ?, ?)""",
            POLICY_PASSAGES,
        )

    counts = {
        "companies": conn.execute("SELECT COUNT(*) FROM COMPANIES").fetchone()[0],
        "transactions": conn.execute("SELECT COUNT(*) FROM TRANSACTIONS").fetchone()[0],
        "alerts_open": conn.execute("SELECT COUNT(*) FROM RISK_ALERTS WHERE STATUS='OPEN'").fetchone()[0],
        "alerts_closed": conn.execute(
            "SELECT COUNT(*) FROM RISK_ALERTS WHERE STATUS != 'OPEN'"
        ).fetchone()[0],
        "policy_passages": conn.execute("SELECT COUNT(*) FROM POLICY_PASSAGES").fetchone()[0],
    }
    print(f"Seeded: {counts}")
    print("Hero alert: ALERT-9001 (sanctions hard override, imminent SLA)")
    print("Contrasting alerts: ALERT-9002 (repeat-escalated override), ALERT-9003 (restricted customer override)")
    print("Held-out / regression alert: ALERT-9004 (clean company, no overrides)")


if __name__ == "__main__":
    main()
