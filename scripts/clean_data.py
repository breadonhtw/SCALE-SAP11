#!/usr/bin/env python3
"""Materialise cleaned snapshot tables from TRUSTSPHERE_REFERENCE into TEAM_11_USER.

Cleaning philosophy (financial-crime data — never invent facts):
- Fix only values that are *derivable* from other columns (e.g. SLA_BREACHED).
- Contradictions that cannot be resolved are flagged, not altered.
- Original source values are preserved alongside any corrected column.
- Every applied rule is logged to TEAM_11_USER.DQ_ISSUES.

Idempotent: drops and recreates all target tables. Server-side CTAS, so no
row data flows through the client.

Usage:
    python scripts/clean_data.py
"""

import json
import os

from hdbcli import dbapi

CREDS_PATH = os.environ.get(
    "TEAM11_CREDS", "/Users/brandon/Desktop/SAP/team-11/team_11_credentials.json"
)
REF = "TRUSTSPHERE_REFERENCE"
TGT = "TEAM_11_USER"

# Alerts from a bulk-close batch share RESOLVED_AT = 2026-06-24 16:17:30 that
# predates CREATED_AT; their resolution timestamps are unusable for durations.
BAD_RESOLVED_PREDICATE = "RESOLVED_AT < CREATED_AT"


def connect():
    with open(CREDS_PATH) as f:
        db = json.load(f)["database"]
    return dbapi.connect(
        address=db["host"],
        port=db["port"],
        user=db["username"],
        password=db["password"],
        encrypt=True,
        sslValidateCertificate=False,
    )


def drop_if_exists(cur, table):
    cur.execute(
        "SELECT COUNT(*) FROM SYS.TABLES WHERE SCHEMA_NAME=? AND TABLE_NAME=?",
        (TGT, table),
    )
    if cur.fetchone()[0]:
        cur.execute(f'DROP TABLE {TGT}."{table}"')


def ctas(cur, table, select_sql):
    drop_if_exists(cur, table)
    cur.execute(f'CREATE COLUMN TABLE {TGT}."{table}" AS ({select_sql})')
    cur.execute(f'SELECT COUNT(*) FROM {TGT}."{table}"')
    n = cur.fetchone()[0]
    print(f"  {TGT}.{table}: {n:,} rows")
    return n


def log_issue(cur, object_name, issue_code, description, affected, action):
    cur.execute(
        f"""INSERT INTO {TGT}.DQ_ISSUES
            (OBJECT_NAME, ISSUE_CODE, DESCRIPTION, AFFECTED_ROWS, ACTION_TAKEN, DETECTED_AT)
            VALUES (?, ?, ?, ?, ?, CURRENT_UTCTIMESTAMP)""",
        (object_name, issue_code, description, affected, action),
    )


def main():
    conn = connect()
    conn.setautocommit(True)
    cur = conn.cursor()

    print("Creating DQ_ISSUES log table...")
    drop_if_exists(cur, "DQ_ISSUES")
    cur.execute(
        f"""CREATE COLUMN TABLE {TGT}.DQ_ISSUES (
            ISSUE_ID INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            OBJECT_NAME NVARCHAR(64) NOT NULL,
            ISSUE_CODE NVARCHAR(64) NOT NULL,
            DESCRIPTION NVARCHAR(1000) NOT NULL,
            AFFECTED_ROWS INTEGER NOT NULL,
            ACTION_TAKEN NVARCHAR(200) NOT NULL,
            DETECTED_AT TIMESTAMP NOT NULL
        )"""
    )

    print("\nCopying clean pass-through tables (verified issue-free by profiling)...")
    for src in [
        "COUNTRIES", "INDUSTRIES", "REGIONS", "SANCTIONS_LISTS", "SCREENING_RULES",
        "TRANSACTIONS", "TRANSACTION_BASELINES", "COMPANY_RISK_PROFILES",
        "COMPLIANCE_CASES", "CASE_ALERTS", "JOULE_EXPLANATIONS",
    ]:
        ctas(cur, src, f"SELECT * FROM {REF}.{src}")

    print("\nCOMPANIES: derive effective KYC status, flag contradictions...")
    # KYC_EFFECTIVE_STATUS: date-driven truth. A verification whose expiry date
    # has passed is EXPIRED regardless of the stale status field; an 'EXPIRED'
    # status with a future expiry date keeps the date-implied VERIFIED reading
    # only if the record was ever verified — we cannot know that, so those rows
    # are flagged and keep their source status.
    ctas(
        cur,
        "COMPANIES",
        f"""SELECT c.*,
            CASE
              WHEN c.KYC_EXPIRY_DATE < CURRENT_DATE AND c.KYC_STATUS IN ('VERIFIED','EXPIRED') THEN 'EXPIRED'
              ELSE c.KYC_STATUS
            END AS KYC_EFFECTIVE_STATUS,
            CASE
              WHEN c.KYC_STATUS = 'VERIFIED' AND c.KYC_EXPIRY_DATE < CURRENT_DATE THEN 'STATUS_STALE_EXPIRED'
              WHEN c.KYC_STATUS = 'EXPIRED' AND c.KYC_EXPIRY_DATE >= CURRENT_DATE THEN 'STATUS_DATE_CONTRADICTION'
              ELSE NULL
            END AS KYC_DQ_FLAG
            FROM {REF}.COMPANIES c""",
    )
    cur.execute(
        f"SELECT COUNT(*) FROM {TGT}.COMPANIES WHERE KYC_DQ_FLAG = 'STATUS_STALE_EXPIRED'"
    )
    n_stale = cur.fetchone()[0]
    cur.execute(
        f"SELECT COUNT(*) FROM {TGT}.COMPANIES WHERE KYC_DQ_FLAG = 'STATUS_DATE_CONTRADICTION'"
    )
    n_contra = cur.fetchone()[0]
    log_issue(cur, "COMPANIES", "KYC_STATUS_STALE",
              "KYC_STATUS='VERIFIED' but KYC_EXPIRY_DATE already passed",
              n_stale, "KYC_EFFECTIVE_STATUS set to EXPIRED; source column preserved")
    log_issue(cur, "COMPANIES", "KYC_STATUS_DATE_CONTRADICTION",
              "KYC_STATUS='EXPIRED' but KYC_EXPIRY_DATE still in the future",
              n_contra, "Flagged only; source status kept (cannot infer verification)")
    log_issue(cur, "COMPANIES", "LEGAL_NAME_NOT_UNIQUE",
              "3,001 rows share a LEGAL_NAME with another company; registration "
              "numbers/LEIs are all distinct so these are distinct legal entities",
              3001, "No change; entity resolution must key on REGISTRATION_NUMBER or LEI_CODE")

    print("\nCOMPANY_BENEFICIAL_OWNERS: flag over-100% ownership companies...")
    ctas(
        cur,
        "COMPANY_BENEFICIAL_OWNERS",
        f"""SELECT b.*,
            CASE WHEN s.TOTAL_PCT > 100.5 THEN 'COMPANY_OWNERSHIP_SUM_EXCEEDS_100' END AS OWNERSHIP_DQ_FLAG,
            s.TOTAL_PCT AS COMPANY_OWNERSHIP_SUM
            FROM {REF}.COMPANY_BENEFICIAL_OWNERS b
            JOIN (SELECT COMPANY_ID, SUM(OWNERSHIP_PERCENTAGE) TOTAL_PCT
                  FROM {REF}.COMPANY_BENEFICIAL_OWNERS GROUP BY COMPANY_ID) s
              ON s.COMPANY_ID = b.COMPANY_ID""",
    )
    cur.execute(
        f"""SELECT COUNT(DISTINCT COMPANY_ID) FROM {TGT}.COMPANY_BENEFICIAL_OWNERS
            WHERE OWNERSHIP_DQ_FLAG IS NOT NULL"""
    )
    n_over = cur.fetchone()[0]
    log_issue(cur, "COMPANY_BENEFICIAL_OWNERS", "OWNERSHIP_SUM_EXCEEDS_100",
              "Company beneficial-ownership percentages sum above 100% (max 109.89%)",
              n_over, "Flagged only; percentages preserved (rescaling would fabricate data)")

    print("\nRISK_ALERTS: recompute SLA_BREACHED, flag corrupt resolution timestamps...")
    # SLA_BREACHED in source only covers currently-open breaches; resolved-late
    # alerts are all FALSE. Recompute from timestamps (the training label for
    # the SLA model). Rows from the corrupt bulk-close batch keep the source
    # flag and get NULL RESOLUTION_HOURS.
    ctas(
        cur,
        "RISK_ALERTS",
        f"""SELECT a.*,
            a.SLA_BREACHED AS SLA_BREACHED_SOURCE,
            CASE
              WHEN {BAD_RESOLVED_PREDICATE} THEN a.SLA_BREACHED
              WHEN a.RESOLVED_AT IS NOT NULL THEN
                CASE WHEN a.RESOLVED_AT > a.SLA_DUE_AT THEN TRUE ELSE FALSE END
              WHEN a.SLA_DUE_AT < CURRENT_UTCTIMESTAMP THEN TRUE
              ELSE FALSE
            END AS SLA_BREACHED_DERIVED,
            CASE
              WHEN {BAD_RESOLVED_PREDICATE} THEN NULL
              WHEN a.RESOLVED_AT IS NOT NULL THEN SECONDS_BETWEEN(a.CREATED_AT, a.RESOLVED_AT) / 3600.0
            END AS RESOLUTION_HOURS,
            CASE WHEN {BAD_RESOLVED_PREDICATE} THEN 'RESOLVED_BEFORE_CREATED' END AS ALERT_DQ_FLAG
            FROM {REF}.RISK_ALERTS a""",
    )
    cur.execute(
        f"""SELECT COUNT(*) FROM {TGT}.RISK_ALERTS
            WHERE SLA_BREACHED_DERIVED <> SLA_BREACHED_SOURCE"""
    )
    n_fixed = cur.fetchone()[0]
    cur.execute(
        f"SELECT COUNT(*) FROM {TGT}.RISK_ALERTS WHERE ALERT_DQ_FLAG IS NOT NULL"
    )
    n_bad_ts = cur.fetchone()[0]
    log_issue(cur, "RISK_ALERTS", "SLA_BREACHED_INCOMPLETE",
              "Source flag only marks currently-open breaches; all historically "
              "resolved-late alerts were FALSE",
              n_fixed, "SLA_BREACHED_DERIVED recomputed from RESOLVED_AT/SLA_DUE_AT; source preserved")
    log_issue(cur, "RISK_ALERTS", "RESOLVED_BEFORE_CREATED",
              "Bulk-close batch stamped RESOLVED_AT=2026-06-24 16:17:30 earlier than CREATED_AT",
              n_bad_ts, "Flagged; RESOLUTION_HOURS set NULL (exclude from duration training)")

    print("\nDQ issue log:")
    cur.execute(
        f"""SELECT OBJECT_NAME, ISSUE_CODE, AFFECTED_ROWS, ACTION_TAKEN
            FROM {TGT}.DQ_ISSUES ORDER BY ISSUE_ID"""
    )
    for r in cur.fetchall():
        print(f"  [{r[0]}] {r[1]} ({r[2]:,} rows) -> {r[3]}")

    conn.close()
    print("\nDone. Cleaned snapshot lives in schema TEAM_11_USER.")


if __name__ == "__main__":
    main()
