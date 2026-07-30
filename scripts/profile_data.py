#!/usr/bin/env python3
"""Profile TRUSTSPHERE_REFERENCE data quality before cleaning.

Read-only: runs diagnostic queries against the reference schema and prints a
report. Cleaning itself happens in scripts/clean_data.py, which materialises
cleaned tables into the writable TEAM_11_USER schema.

Usage:
    python scripts/profile_data.py
"""

import json
import os

from hdbcli import dbapi

CREDS_PATH = os.environ.get(
    "TEAM11_CREDS", "/Users/brandon/Desktop/SAP/team-11/team_11_credentials.json"
)
REF = "TRUSTSPHERE_REFERENCE"


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


def q(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def one(cur, sql, params=()):
    return q(cur, sql, params)[0][0]


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def check(cur, label, sql):
    n = one(cur, sql)
    flag = "⚠️ " if n else "   "
    print(f"{flag}{label}: {n:,}")
    return n


def main():
    conn = connect()
    cur = conn.cursor()

    section("TRANSACTIONS (150k)")
    t = f"{REF}.TRANSACTIONS"
    check(cur, "duplicate TRANSACTION_ID", f"SELECT COUNT(*) - COUNT(DISTINCT TRANSACTION_ID) FROM {t}")
    check(cur, "duplicate TRANSACTION_UUID", f"SELECT COUNT(*) - COUNT(DISTINCT TRANSACTION_UUID) FROM {t}")
    check(cur, "duplicate TRANSACTION_REF", f"SELECT COUNT(*) - COUNT(DISTINCT TRANSACTION_REF) FROM {t}")
    for col in ["ORIGINATOR_COMPANY_ID", "BENEFICIARY_NAME", "AMOUNT_ORIGINAL", "AMOUNT_USD",
                "CURRENCY_ORIGINAL", "INITIATED_AT", "STATUS", "ORIGINATING_COUNTRY_ID",
                "DESTINATION_COUNTRY_ID", "BENEFICIARY_COUNTRY_ID", "EXCHANGE_RATE",
                "TRANSACTION_TYPE", "PAYMENT_PURPOSE", "SETTLED_AT", "VALUE_DATE"]:
        check(cur, f"NULL {col}", f"SELECT COUNT(*) FROM {t} WHERE {col} IS NULL")
    check(cur, "AMOUNT_USD <= 0", f"SELECT COUNT(*) FROM {t} WHERE AMOUNT_USD <= 0")
    check(cur, "AMOUNT_ORIGINAL <= 0", f"SELECT COUNT(*) FROM {t} WHERE AMOUNT_ORIGINAL <= 0")
    check(cur, "USD amount mismatch >1% (orig*rate vs USD)",
          f"SELECT COUNT(*) FROM {t} WHERE EXCHANGE_RATE IS NOT NULL AND AMOUNT_USD IS NOT NULL "
          f"AND AMOUNT_ORIGINAL IS NOT NULL AND EXCHANGE_RATE > 0 "
          f"AND ABS(AMOUNT_ORIGINAL * EXCHANGE_RATE - AMOUNT_USD) / AMOUNT_USD > 0.01")
    check(cur, "SETTLED_AT before INITIATED_AT", f"SELECT COUNT(*) FROM {t} WHERE SETTLED_AT < INITIATED_AT")
    check(cur, "INITIATED_AT in the future", f"SELECT COUNT(*) FROM {t} WHERE INITIATED_AT > CURRENT_UTCTIMESTAMP")
    check(cur, "orphan ORIGINATOR_COMPANY_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.COMPANIES c ON x.ORIGINATOR_COMPANY_ID=c.COMPANY_ID "
          f"WHERE x.ORIGINATOR_COMPANY_ID IS NOT NULL AND c.COMPANY_ID IS NULL")
    check(cur, "orphan BENEFICIARY_COMPANY_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.COMPANIES c ON x.BENEFICIARY_COMPANY_ID=c.COMPANY_ID "
          f"WHERE x.BENEFICIARY_COMPANY_ID IS NOT NULL AND c.COMPANY_ID IS NULL")
    check(cur, "orphan ORIGINATING_COUNTRY_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.COUNTRIES k ON x.ORIGINATING_COUNTRY_ID=k.COUNTRY_ID "
          f"WHERE x.ORIGINATING_COUNTRY_ID IS NOT NULL AND k.COUNTRY_ID IS NULL")
    check(cur, "orphan DESTINATION_COUNTRY_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.COUNTRIES k ON x.DESTINATION_COUNTRY_ID=k.COUNTRY_ID "
          f"WHERE x.DESTINATION_COUNTRY_ID IS NOT NULL AND k.COUNTRY_ID IS NULL")
    check(cur, "IS_CROSS_BORDER inconsistent with countries",
          f"SELECT COUNT(*) FROM {t} WHERE ORIGINATING_COUNTRY_ID IS NOT NULL AND DESTINATION_COUNTRY_ID IS NOT NULL "
          f"AND IS_CROSS_BORDER <> CASE WHEN ORIGINATING_COUNTRY_ID <> DESTINATION_COUNTRY_ID THEN TRUE ELSE FALSE END")
    print("\nSTATUS values:")
    for s, n in q(cur, f"SELECT STATUS, COUNT(*) FROM {t} GROUP BY STATUS ORDER BY 2 DESC"):
        print(f"     {s!r}: {n:,}")
    print("CURRENCY values:")
    for s, n in q(cur, f"SELECT CURRENCY_ORIGINAL, COUNT(*) FROM {t} GROUP BY CURRENCY_ORIGINAL ORDER BY 2 DESC"):
        print(f"     {s!r}: {n:,}")
    print("TRANSACTION_TYPE values:")
    for s, n in q(cur, f"SELECT TRANSACTION_TYPE, COUNT(*) FROM {t} GROUP BY TRANSACTION_TYPE ORDER BY 2 DESC"):
        print(f"     {s!r}: {n:,}")

    section("COMPANIES (5k)")
    t = f"{REF}.COMPANIES"
    check(cur, "duplicate COMPANY_ID", f"SELECT COUNT(*) - COUNT(DISTINCT COMPANY_ID) FROM {t}")
    check(cur, "duplicate LEGAL_NAME", f"SELECT COUNT(*) - COUNT(DISTINCT LEGAL_NAME) FROM {t}")
    for col in ["LEGAL_NAME", "INCORPORATION_COUNTRY_ID", "HEADQUARTERS_COUNTRY_ID", "INDUSTRY_ID",
                "KYC_STATUS", "KYC_RISK_RATING", "KYC_VERIFIED_DATE", "KYC_EXPIRY_DATE",
                "ANNUAL_REVENUE_USD", "EMPLOYEE_COUNT", "CLIENT_SEGMENT"]:
        check(cur, f"NULL {col}", f"SELECT COUNT(*) FROM {t} WHERE {col} IS NULL")
    check(cur, "KYC_EXPIRY before KYC_VERIFIED",
          f"SELECT COUNT(*) FROM {t} WHERE KYC_EXPIRY_DATE < KYC_VERIFIED_DATE")
    check(cur, "KYC expired (vs today)", f"SELECT COUNT(*) FROM {t} WHERE KYC_EXPIRY_DATE < CURRENT_DATE")
    check(cur, "EMPLOYEE_COUNT <= 0", f"SELECT COUNT(*) FROM {t} WHERE EMPLOYEE_COUNT <= 0")
    check(cur, "ANNUAL_REVENUE_USD < 0", f"SELECT COUNT(*) FROM {t} WHERE ANNUAL_REVENUE_USD < 0")
    check(cur, "orphan INDUSTRY_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.INDUSTRIES i ON x.INDUSTRY_ID=i.INDUSTRY_ID "
          f"WHERE x.INDUSTRY_ID IS NOT NULL AND i.INDUSTRY_ID IS NULL")
    print("\nKYC_STATUS values:")
    for s, n in q(cur, f"SELECT KYC_STATUS, COUNT(*) FROM {t} GROUP BY KYC_STATUS ORDER BY 2 DESC"):
        print(f"     {s!r}: {n:,}")
    print("KYC_RISK_RATING values:")
    for s, n in q(cur, f"SELECT KYC_RISK_RATING, COUNT(*) FROM {t} GROUP BY KYC_RISK_RATING ORDER BY 2 DESC"):
        print(f"     {s!r}: {n:,}")

    section("COMPANY_BENEFICIAL_OWNERS (12.5k)")
    t = f"{REF}.COMPANY_BENEFICIAL_OWNERS"
    check(cur, "orphan COMPANY_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.COMPANIES c ON x.COMPANY_ID=c.COMPANY_ID WHERE c.COMPANY_ID IS NULL")
    check(cur, "OWNERSHIP_PERCENTAGE out of (0,100]",
          f"SELECT COUNT(*) FROM {t} WHERE OWNERSHIP_PERCENTAGE <= 0 OR OWNERSHIP_PERCENTAGE > 100")
    check(cur, "NULL OWNER_NAME", f"SELECT COUNT(*) FROM {t} WHERE OWNER_NAME IS NULL")
    check(cur, "companies with ownership sum > 105%",
          f"SELECT COUNT(*) FROM (SELECT COMPANY_ID FROM {t} GROUP BY COMPANY_ID HAVING SUM(OWNERSHIP_PERCENTAGE) > 105)")
    check(cur, "duplicate (COMPANY_ID, OWNER_NAME)",
          f"SELECT COUNT(*) FROM (SELECT COMPANY_ID, OWNER_NAME, COUNT(*) c FROM {t} GROUP BY COMPANY_ID, OWNER_NAME HAVING COUNT(*) > 1)")

    section("RISK_ALERTS (5k)")
    t = f"{REF}.RISK_ALERTS"
    check(cur, "duplicate ALERT_ID", f"SELECT COUNT(*) - COUNT(DISTINCT ALERT_ID) FROM {t}")
    for col in ["ALERT_TYPE", "ALERT_PRIORITY", "STATUS", "SLA_DUE_AT", "CREATED_AT"]:
        check(cur, f"NULL {col}", f"SELECT COUNT(*) FROM {t} WHERE {col} IS NULL")
    check(cur, "orphan TRANSACTION_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.TRANSACTIONS r ON x.TRANSACTION_ID=r.TRANSACTION_ID "
          f"WHERE x.TRANSACTION_ID IS NOT NULL AND r.TRANSACTION_ID IS NULL")
    check(cur, "orphan COMPANY_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.COMPANIES c ON x.COMPANY_ID=c.COMPANY_ID "
          f"WHERE x.COMPANY_ID IS NOT NULL AND c.COMPANY_ID IS NULL")
    check(cur, "RESOLVED_AT before CREATED_AT", f"SELECT COUNT(*) FROM {t} WHERE RESOLVED_AT < CREATED_AT")
    check(cur, "resolved status but NULL RESOLVED_AT",
          f"SELECT COUNT(*) FROM {t} WHERE STATUS IN ('RESOLVED','CLOSED','DISMISSED') AND RESOLVED_AT IS NULL")
    check(cur, "open status but RESOLVED_AT set",
          f"SELECT COUNT(*) FROM {t} WHERE STATUS IN ('OPEN','NEW','IN_PROGRESS','INVESTIGATING') AND RESOLVED_AT IS NOT NULL")
    check(cur, "SLA_BREACHED flag inconsistent (resolved late but not flagged)",
          f"SELECT COUNT(*) FROM {t} WHERE RESOLVED_AT > SLA_DUE_AT AND SLA_BREACHED = FALSE")
    check(cur, "SLA_BREACHED flag inconsistent (unresolved past due but not flagged)",
          f"SELECT COUNT(*) FROM {t} WHERE RESOLVED_AT IS NULL AND SLA_DUE_AT < CURRENT_UTCTIMESTAMP AND SLA_BREACHED = FALSE")
    print("\nSTATUS values:")
    for s, n in q(cur, f"SELECT STATUS, COUNT(*) FROM {t} GROUP BY STATUS ORDER BY 2 DESC"):
        print(f"     {s!r}: {n:,}")
    print("ALERT_TYPE values:")
    for s, n in q(cur, f"SELECT ALERT_TYPE, COUNT(*) FROM {t} GROUP BY ALERT_TYPE ORDER BY 2 DESC"):
        print(f"     {s!r}: {n:,}")
    print("ALERT_PRIORITY values:")
    for s, n in q(cur, f"SELECT ALERT_PRIORITY, COUNT(*) FROM {t} GROUP BY ALERT_PRIORITY ORDER BY 2 DESC"):
        print(f"     {s!r}: {n:,}")

    section("COMPLIANCE_CASES (500) + CASE_ALERTS (580)")
    t = f"{REF}.COMPLIANCE_CASES"
    check(cur, "duplicate CASE_ID", f"SELECT COUNT(*) - COUNT(DISTINCT CASE_ID) FROM {t}")
    check(cur, "orphan COMPANY_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.COMPANIES c ON x.COMPANY_ID=c.COMPANY_ID "
          f"WHERE x.COMPANY_ID IS NOT NULL AND c.COMPANY_ID IS NULL")
    check(cur, "CLOSED_AT before OPENED_AT", f"SELECT COUNT(*) FROM {t} WHERE CLOSED_AT < OPENED_AT")
    check(cur, "SAR_FILED but NULL SAR_REFERENCE",
          f"SELECT COUNT(*) FROM {t} WHERE SAR_FILED = TRUE AND SAR_REFERENCE IS NULL")
    check(cur, "closed status but NULL CLOSED_AT",
          f"SELECT COUNT(*) FROM {t} WHERE STATUS IN ('CLOSED','RESOLVED') AND CLOSED_AT IS NULL")
    ca = f"{REF}.CASE_ALERTS"
    check(cur, "CASE_ALERTS orphan CASE_ID",
          f"SELECT COUNT(*) FROM {ca} x LEFT JOIN {t} c ON x.CASE_ID=c.CASE_ID WHERE c.CASE_ID IS NULL")
    check(cur, "CASE_ALERTS orphan ALERT_ID",
          f"SELECT COUNT(*) FROM {ca} x LEFT JOIN {REF}.RISK_ALERTS a ON x.ALERT_ID=a.ALERT_ID WHERE a.ALERT_ID IS NULL")
    print("\nCASE STATUS values:")
    for s, n in q(cur, f"SELECT STATUS, COUNT(*) FROM {t} GROUP BY STATUS ORDER BY 2 DESC"):
        print(f"     {s!r}: {n:,}")

    section("TRANSACTION_BASELINES (150k)")
    t = f"{REF}.TRANSACTION_BASELINES"
    check(cur, "orphan COMPANY_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.COMPANIES c ON x.COMPANY_ID=c.COMPANY_ID WHERE c.COMPANY_ID IS NULL")
    check(cur, "PERIOD_END before PERIOD_START", f"SELECT COUNT(*) FROM {t} WHERE PERIOD_END < PERIOD_START")
    check(cur, "negative amounts",
          f"SELECT COUNT(*) FROM {t} WHERE TOTAL_AMOUNT_USD < 0 OR AVG_AMOUNT_USD < 0 OR MIN_AMOUNT_USD < 0")
    check(cur, "MAX < MIN amount", f"SELECT COUNT(*) FROM {t} WHERE MAX_AMOUNT_USD < MIN_AMOUNT_USD")
    check(cur, "duplicate (COMPANY_ID, BASELINE_PERIOD, PERIOD_START)",
          f"SELECT COUNT(*) FROM (SELECT COMPANY_ID, BASELINE_PERIOD, PERIOD_START, COUNT(*) c FROM {t} "
          f"GROUP BY COMPANY_ID, BASELINE_PERIOD, PERIOD_START HAVING COUNT(*) > 1)")

    section("COMPANY_RISK_PROFILES (5k)")
    t = f"{REF}.COMPANY_RISK_PROFILES"
    check(cur, "orphan COMPANY_ID",
          f"SELECT COUNT(*) FROM {t} x LEFT JOIN {REF}.COMPANIES c ON x.COMPANY_ID=c.COMPANY_ID WHERE c.COMPANY_ID IS NULL")
    check(cur, "duplicate COMPANY_ID (multiple profiles)",
          f"SELECT COUNT(*) FROM (SELECT COMPANY_ID FROM {t} GROUP BY COMPANY_ID HAVING COUNT(*) > 1)")
    check(cur, "scores out of [0,100]",
          f"SELECT COUNT(*) FROM {t} WHERE COMPOSITE_RISK_SCORE < 0 OR COMPOSITE_RISK_SCORE > 100 "
          f"OR COUNTRY_RISK_SCORE < 0 OR COUNTRY_RISK_SCORE > 100")
    print("\nRISK_TIER values:")
    for s, n in q(cur, f"SELECT RISK_TIER, COUNT(*) FROM {t} GROUP BY RISK_TIER ORDER BY 2 DESC"):
        print(f"     {s!r}: {n:,}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
