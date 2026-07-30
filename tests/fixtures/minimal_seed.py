"""Minimal fixture seeding for integration tests — a tiny subset of
scripts/seed_local_demo.py's shape, just enough for repository/API tests to
exercise real SQL (joins, ordering) without depending on the full demo
dataset. Kept alongside it rather than importing it, so demo-data changes
don't silently change test behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trustsphere.persistence.local import LocalSQLiteRepository

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


def seed_minimal(repo: LocalSQLiteRepository) -> None:
    conn = repo.conn
    with conn:
        conn.execute(
            "INSERT INTO COUNTRIES (COUNTRY_ID, COUNTRY_NAME, RISK_RATING) VALUES ('SGP','Singapore','LOW')"
        )
        conn.execute(
            """INSERT INTO COMPANIES
               (COMPANY_ID, LEGAL_NAME, REGISTRATION_NUMBER, KYC_EFFECTIVE_STATUS, CLIENT_SEGMENT, RESTRICTED_FLAG)
               VALUES ('CMP-T1', 'Test Trading Pte Ltd', 'SG-T1', 'VERIFIED', 'CORPORATE', 0)"""
        )
        conn.execute(
            """INSERT INTO COMPANY_RISK_PROFILES (COMPANY_ID, COMPOSITE_RISK_SCORE, COUNTRY_RISK_SCORE, RISK_TIER)
               VALUES ('CMP-T1', 50, 30, 'MEDIUM')"""
        )
        conn.execute(
            """INSERT INTO TRANSACTIONS
               (TRANSACTION_ID, ORIGINATOR_COMPANY_ID, BENEFICIARY_NAME, AMOUNT_ORIGINAL, AMOUNT_USD,
                CURRENCY_ORIGINAL, INITIATED_AT, STATUS, ORIGINATING_COUNTRY_ID, DESTINATION_COUNTRY_ID,
                IS_CROSS_BORDER)
               VALUES ('TXN-T1', 'CMP-T1', 'Counterparty Co', 20000, 20000, 'SGD', ?, 'SETTLED', 'SGP', 'SGP', 0)""",
            (NOW.isoformat(),),
        )
        conn.execute(
            """INSERT INTO TRANSACTION_BASELINES (COMPANY_ID, BASELINE_PERIOD, PERIOD_START, PERIOD_END, AVG_AMOUNT_USD)
               VALUES ('CMP-T1', 'TRAILING_90D', ?, ?, 18000)""",
            ((NOW - timedelta(days=90)).isoformat(), NOW.isoformat()),
        )
        # Two open alerts, different urgency shape, for queue-ordering tests.
        conn.execute(
            """INSERT INTO RISK_ALERTS (ALERT_ID, COMPANY_ID, TRANSACTION_ID, ALERT_TYPE, ALERT_PRIORITY,
                                          STATUS, CREATED_AT, SLA_DUE_AT)
               VALUES ('ALERT-T1', 'CMP-T1', 'TXN-T1', 'UNUSUAL_TRANSACTION_VELOCITY', 'MEDIUM', 'OPEN', ?, ?)""",
            ((NOW - timedelta(hours=5)).isoformat(), (NOW + timedelta(hours=60)).isoformat()),
        )
        conn.execute(
            """INSERT INTO RISK_ALERTS (ALERT_ID, COMPANY_ID, TRANSACTION_ID, ALERT_TYPE, ALERT_PRIORITY,
                                          STATUS, CREATED_AT, SLA_DUE_AT)
               VALUES ('ALERT-T2', 'CMP-T1', 'TXN-T1', 'STRUCTURING_PATTERN', 'HIGH', 'OPEN', ?, ?)""",
            ((NOW - timedelta(hours=1)).isoformat(), (NOW + timedelta(hours=2)).isoformat()),
        )
        # A closed historical alert — must never appear in the ranked queue.
        conn.execute(
            """INSERT INTO RISK_ALERTS (ALERT_ID, COMPANY_ID, ALERT_TYPE, ALERT_PRIORITY, STATUS,
                                          CREATED_AT, SLA_DUE_AT, RESOLVED_AT, RESOLUTION_HOURS)
               VALUES ('ALERT-T3', 'CMP-T1', 'UNUSUAL_TRANSACTION_VELOCITY', 'LOW', 'CLOSED_TRUE', ?, ?, ?, 24.0)""",
            (
                (NOW - timedelta(days=10)).isoformat(),
                (NOW - timedelta(days=9)).isoformat(),
                (NOW - timedelta(days=9)).isoformat(),
            ),
        )
