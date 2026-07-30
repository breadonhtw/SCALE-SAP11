#!/usr/bin/env python
"""Reset app-state tables to a pristine pre-demo slate.

Deletes application-created rows (cases, case files, citations, drafts,
decisions, workflows, audit events, idempotency keys) from the ACTIVE
backend (DATA_BACKEND in .env — HANA or local). Source/reference tables and
PRIORITY_SCORES are untouched, so the ranked queue keeps working.

Governance note (CLAUDE.md §0.1): this is disclosed development hygiene for
a prototype — the rows removed are build/test artifacts, not investigation
history. Production audit rows are append-only and would never be wiped.

Usage:
    python scripts/reset_demo_state.py             # prompts for confirmation
    python scripts/reset_demo_state.py --yes       # no prompt (CI/demo prep)
    python scripts/reset_demo_state.py --dry-run   # counts only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trustsphere.api.deps import get_repo  # noqa: E402

APP_STATE_TABLES = [
    "AUDIT_EVENTS", "DECISIONS", "WORKFLOW_INSTANCES", "NARRATIVE_DRAFTS",
    "SOURCE_CITATIONS", "CASE_FILES", "CASES", "IDEMPOTENCY_KEYS",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="skip confirmation")
    parser.add_argument("--dry-run", action="store_true", help="counts only")
    args = parser.parse_args()

    repo = get_repo()
    label = repo.backend_label()
    print(f"Backend: {label}")

    # Count first so the operator sees what would go.
    counts = {}
    for table in APP_STATE_TABLES:
        if hasattr(repo, "_q"):        # HANA
            rows = repo._q(f"SELECT COUNT(*) AS N FROM {repo._t(table)}")
            counts[table] = int(rows[0]["N"])
        else:                           # local sqlite
            counts[table] = int(repo.conn.execute(
                f"SELECT COUNT(*) AS N FROM {table}").fetchone()["N"])
    for table, n in counts.items():
        print(f"  {table}: {n} rows")

    if args.dry_run:
        print("Dry run — nothing deleted.")
        return 0
    if not args.yes:
        answer = input(f"Delete these rows from {label}? [y/N] ")
        if answer.strip().lower() != "y":
            print("Aborted.")
            return 1

    if hasattr(repo, "_q"):
        cur = repo.conn.cursor()
        for table in APP_STATE_TABLES:
            cur.execute(f"DELETE FROM {repo._t(table)}")
        repo.conn.commit()
    else:
        with repo.conn:
            for table in APP_STATE_TABLES:
                repo.conn.execute(f"DELETE FROM {table}")
    print("App state reset. PRIORITY_SCORES retained — queue stays ranked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
