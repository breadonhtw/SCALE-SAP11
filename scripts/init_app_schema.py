#!/usr/bin/env python3
"""Idempotently apply migrations/001_app_schema.sql to SAP HANA Cloud
(schema TEAM_11_USER).

Referenced by the comment at the top of migrations/001_app_schema.sql: run
this once against the tenant to create the application-state tables (CASES,
PRIORITY_SCORES, ALERT_FACTORS, PREDICTIVE_SCORES, CASE_FILES,
SOURCE_CITATIONS, NARRATIVE_DRAFTS, DECISIONS, WORKFLOW_INSTANCES,
AUDIT_EVENTS, IDEMPOTENCY_KEYS, POLICY_PASSAGES) on top of the already
materialised cleaned business snapshot (scripts/clean_data.py).

Idempotent by design: checks SYS.TABLES for each table name before running
its CREATE COLUMN TABLE statement, and skips it (with a log line) if the
table already exists — CLAUDE.md §23 "make create/start operations
idempotent" and the migration file's own stated contract. Safe to re-run.

Usage:
    python scripts/init_app_schema.py
    python scripts/init_app_schema.py --dry-run   # print planned actions only

Requires HANA credentials via TEAM11_CREDS or HANA_HOST/HANA_USER/
HANA_PASSWORD (see .env.example). Uses DATA_BACKEND=hana regardless of what
is set in .env, since this script's entire purpose is to prepare the HANA
schema.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trustsphere.config.settings import Settings  # noqa: E402
from trustsphere.persistence.hana import HanaRepository  # noqa: E402

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "001_app_schema.sql"

# Matches: CREATE COLUMN TABLE TEAM_11_USER."NAME" ( ... ) ; — statements are
# separated on a line consisting of ');' or ');\n' followed by a comment/CREATE.
_CREATE_TABLE_RE = re.compile(
    r'CREATE COLUMN TABLE\s+\S+\."(?P<name>[A-Z0-9_]+)"\s*\((?P<body>.*?)\n\);',
    re.DOTALL,
)


def parse_statements(sql_text: str) -> list[tuple[str, str]]:
    """Returns [(table_name, full_create_statement), ...] in file order."""
    statements = []
    for m in _CREATE_TABLE_RE.finditer(sql_text):
        statements.append((m.group("name"), m.group(0)))
    return statements


def table_exists(cur, schema: str, table: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM SYS.TABLES WHERE SCHEMA_NAME = ? AND TABLE_NAME = ?",
        (schema, table),
    )
    return cur.fetchone()[0] > 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions, execute nothing")
    args = parser.parse_args()

    settings = Settings(data_backend="hana")
    repo = HanaRepository(settings)
    schema = settings.hana_schema

    sql_text = MIGRATION_PATH.read_text()
    statements = parse_statements(sql_text)
    if not statements:
        raise RuntimeError(f"No CREATE COLUMN TABLE statements parsed from {MIGRATION_PATH}")

    print(f"Target schema: {schema}  ({len(statements)} tables in migration file)")

    cur = repo.conn.cursor()
    created, skipped = [], []
    for table_name, statement in statements:
        if table_exists(cur, schema, table_name):
            print(f"  = {table_name} already exists — skipping")
            skipped.append(table_name)
            continue
        print(f"  + {table_name} does not exist — {'would create' if args.dry_run else 'creating'}")
        if not args.dry_run:
            cur.execute(statement)
            repo.conn.commit()
        created.append(table_name)

    print(f"\n{'Would create' if args.dry_run else 'Created'}: {created}")
    print(f"Already present (skipped): {skipped}")
    cur.close()


if __name__ == "__main__":
    main()
