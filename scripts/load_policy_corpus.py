#!/usr/bin/env python3
"""Populate TEAM_11_USER.POLICY_PASSAGES with in-DB HANA vector embeddings.

Uses SAP HANA Cloud's native VECTOR_EMBEDDING (SAP_NEB model, verified
capability — docs/capability-matrix.md) to embed each policy passage
server-side. No external embedding pipeline: the text goes to HANA, the
768-dim REAL_VECTOR comes back and is stored in the same INSERT. This is
what makes `retrieval/vector.py`'s `search_policy_passages_native` path
(COSINE_SIMILARITY over VECTOR_EMBEDDING) honestly claimable as "HANA
vector retrieval" rather than the local TF-IDF fallback.

The corpus below is a small, clearly-illustrative starter set covering the
policy areas the hero + contrasting + held-out demo alerts actually touch
(sanctions escalation, missing-information handling, structuring/large-cash
rule intent, SLA/ageing safeguards, high-risk jurisdiction corridors, KYC
expiry handling, PEP association review, and human decision authority). It
is NOT a legal or regulatory corpus — CLAUDE.md §9 "initial policy
assumptions, not discovered truth" applies here too.

Idempotent: deletes any existing rows for a DOCUMENT_ID before re-inserting,
so re-running after editing POLICY_CORPUS below is safe.

Usage:
    python scripts/load_policy_corpus.py
    python scripts/load_policy_corpus.py --verify "sanctions match escalation"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trustsphere.config.settings import Settings  # noqa: E402
from trustsphere.persistence.hana import HanaRepository  # noqa: E402

EMBEDDING_MODEL = "SAP_NEB.20240715"

# document_id, passage_locator, text, doc_type, region, effective_date (YYYY-MM-DD)
POLICY_CORPUS = [
    ("POL-AML-001", "SEC-3.2",
     "Sanctions Screening Escalation. Any confirmed match against an active sanctions list "
     "requires immediate escalation to senior compliance review and forced CRITICAL "
     "prioritisation, regardless of the alert's computed urgency score. Escalation must occur "
     "within 4 working hours of match confirmation.",
     "sanctions_policy", "APJ", "2025-01-01"),
    ("POL-AML-002", "SEC-5.1",
     "Missing Information Handling. Where a required data element such as KYC status, "
     "beneficial ownership, or transaction baseline cannot be retrieved, the investigator must "
     "record the gap explicitly in the case file and may not infer or estimate the missing "
     "value. Cases with unresolved critical fields should be flagged for supervisory review "
     "before closure.",
     "procedure", "APJ", "2025-01-01"),
    ("POL-AML-003", "SEC-2.4",
     "Structuring and Large-Cash Monitoring Intent. The structuring monitoring rule is intended "
     "to detect patterns of transactions kept below reporting thresholds, or single large "
     "transactions materially inconsistent with a customer's established baseline. A ratio "
     "above eight times the customer's average transaction size is treated as high materiality.",
     "rule_intent", "APJ", "2025-01-01"),
    ("POL-AML-004", "SEC-6.3",
     "SLA and Ageing Safeguards. Alerts within twenty-four hours of their SLA due date must be "
     "treated as near-breach and prioritised accordingly. A small sample of low-ranked alerts "
     "must be periodically pulled into review to prevent indefinite ageing in the queue.",
     "sla_policy", "APJ", "2025-01-01"),
    ("POL-AML-005", "SEC-4.7",
     "High-Risk Jurisdiction Corridors. Transactions crossing into or out of jurisdictions "
     "rated HIGH or CRITICAL risk tier, particularly involving Cayman Islands or Panama "
     "intermediary entities, require documented review of beneficial ownership and counterparty "
     "purpose before an alert may be closed.",
     "jurisdiction_policy", "APJ", "2025-01-01"),
    ("POL-AML-006", "SEC-5.4",
     "KYC Expiry and Remediation. A customer with an EXPIRED or REJECTED KYC effective status "
     "increases the entity-risk factor of any linked alert and must not be treated as verified "
     "for prioritisation purposes until KYC remediation is confirmed and re-dated.",
     "kyc_policy", "APJ", "2025-01-01"),
    ("POL-AML-007", "SEC-3.9",
     "PEP Association Review. An alert linking a customer or beneficial owner to a politically "
     "exposed person does not by itself constitute wrongdoing. It requires a documented review "
     "of the relationship's business rationale, source of wealth, and any prior enhanced "
     "due-diligence findings before the alert can be closed.",
     "pep_policy", "APJ", "2025-01-01"),
    ("POL-AML-008", "SEC-7.1",
     "Human Decision Authority. Only a human investigator may approve escalation, return a case "
     "for further edit, request additional information, or attest to a case file's completeness. "
     "Generated narratives and AI-assisted scores are decision support only and are never "
     "auto-approved.",
     "governance_policy", "APJ", "2025-01-01"),
]


def load(dry_run: bool = False) -> None:
    settings = Settings(data_backend="hana")
    repo = HanaRepository(settings)
    schema = settings.hana_schema
    cur = repo.conn.cursor()

    for doc_id, locator, text, doc_type, region, effective_date in POLICY_CORPUS:
        if dry_run:
            print(f"  would load {doc_id}/{locator} ({len(text)} chars, doc_type={doc_type})")
            continue
        cur.execute(
            f'DELETE FROM {schema}."POLICY_PASSAGES" WHERE DOCUMENT_ID = ? AND PASSAGE_LOCATOR = ?',
            (doc_id, locator),
        )
        cur.execute(
            f"""INSERT INTO {schema}."POLICY_PASSAGES"
                (DOCUMENT_ID, PASSAGE_LOCATOR, TEXT_CONTENT, DOC_TYPE, REGION, EFFECTIVE_DATE, EMBEDDING)
                VALUES (?, ?, ?, ?, ?, TO_DATE(?, 'YYYY-MM-DD'), VECTOR_EMBEDDING(?, 'DOCUMENT', ?))""",
            (doc_id, locator, text, doc_type, region, effective_date, text, EMBEDDING_MODEL),
        )
        print(f"  loaded {doc_id}/{locator}")
    if not dry_run:
        repo.conn.commit()
    cur.close()


def verify(query_text: str) -> None:
    settings = Settings(data_backend="hana")
    repo = HanaRepository(settings)
    rows = repo.search_policy_passages_native(query_text, limit=3)
    print(f"\nTop matches for {query_text!r} (HANA-native VECTOR_EMBEDDING + COSINE_SIMILARITY):")
    for r in rows:
        print(f"  [{r['SIMILARITY']:.4f}] {r['DOCUMENT_ID']}/{r['PASSAGE_LOCATOR']}: {r['TEXT'][:90]}...")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions, execute nothing")
    parser.add_argument("--verify", metavar="QUERY_TEXT", help="After loading, run one native similarity query as proof")
    args = parser.parse_args()

    print(f"Loading {len(POLICY_CORPUS)} policy passages with server-side {EMBEDDING_MODEL} embeddings")
    load(dry_run=args.dry_run)

    if args.verify and not args.dry_run:
        verify(args.verify)


if __name__ == "__main__":
    main()
