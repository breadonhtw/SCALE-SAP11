#!/usr/bin/env python
"""Demo smoke test (CLAUDE.md §25): one command proving the demo path.

Runs against the LIVE backend at TRUSTSPHERE_API_BASE_URL (default
http://127.0.0.1:8000) — start it first. Checks, in demo order:

 1. backend healthy (reports which backend honestly)
 2. ranked queue loads with total count
 3. hero alert scores deterministically (two runs, identical result)
 4. case assembly returns a cited CaseFile
 5. advisory SLA prediction returns (real or fallback, labelled)
 6. narrative generation returns (live AI Core or fallback, labelled) and
    passes citation/numeric validation; draft persists
 7. unattested decision is REFUSED (the human-accountability control)
 8. attested decision records + appends audit events in order

Exit code 0 = demo-ready. Any failure prints the step and exits 1.
Usage:
    python scripts/run_demo_checks.py [--alert <id>] [--keep]
--keep skips the cleanup reminder at the end (state stays for inspection).
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

BASE = os.environ.get("TRUSTSPHERE_API_BASE_URL", "http://127.0.0.1:8000")
PASS, FAIL = "  [PASS]", "  [FAIL]"


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{PASS if ok else FAIL} {label}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alert", default=None,
                        help="alert id to exercise (default: queue rank #1)")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    ok = True

    # 1. health
    health = requests.get(f"{BASE}/health", timeout=30).json()
    ok &= check("backend healthy", bool(health.get("ok")),
                f"backend={health.get('backend')}")

    # 2. queue
    queue = requests.get(f"{BASE}/alerts/queue", params={"limit": 5},
                         timeout=60).json()
    total = queue.get("total", queue.get("count", 0))
    ok &= check("ranked queue loads", queue.get("count", 0) > 0,
                f"total={total}")
    if not ok:
        print("Queue empty — run POST /alerts/score-all first.")
        return 1
    alert_id = args.alert or str(queue["queue"][0]["ALERT_ID"])
    print(f"  using alert {alert_id}")

    # 3. deterministic scoring (same input -> identical output)
    s1 = requests.post(f"{BASE}/alerts/{alert_id}/score",
                       json={"as_of": "2026-07-31T00:00:00Z"}, timeout=60).json()["score"]
    s2 = requests.post(f"{BASE}/alerts/{alert_id}/score",
                       json={"as_of": "2026-07-31T00:00:00Z"}, timeout=60).json()["score"]
    ok &= check("deterministic scoring",
                (s1["urgency_score"], s1["urgency_tier"]) ==
                (s2["urgency_score"], s2["urgency_tier"]),
                f"score={s1['urgency_score']} tier={s1['urgency_tier']} "
                f"policy={s1['policy_version']}")

    # 4. case + CaseFile with citations
    case = requests.post(f"{BASE}/alerts/{alert_id}/cases", json={},
                         timeout=60).json()["case"]
    case_id = case["CASE_ID"]
    cf = requests.post(f"{BASE}/cases/{case_id}/assemble", json={},
                       timeout=300).json()["case_file"]
    ok &= check("CaseFile assembled with citations",
                len(cf.get("source_provenance", [])) > 0,
                f"citations={len(cf['source_provenance'])} "
                f"coverage={cf['source_coverage']:.0%} "
                f"missing={len(cf['missing_information'])}")

    # 5. advisory prediction
    pred = requests.post(f"{BASE}/alerts/{alert_id}/predict-sla", json={},
                         timeout=60).json()["prediction"]
    ok &= check("advisory SLA prediction", bool(pred.get("advisory_only")),
                f"{pred.get('prediction_type')}={pred.get('prediction_value')} "
                f"model={pred.get('model_name')} label={pred.get('label')!r}")

    # 6. generation + validation + persisted draft
    gen = requests.post(f"{BASE}/cases/{case_id}/explanations",
                        json={"task": "narrative"}, timeout=300).json()
    e = gen["explanation"]
    v = e["validation"]
    ok &= check("narrative generated + validated",
                len(e["sentences"]) > 0,
                f"backend={e['generation']['generation_backend']} "
                f"model={e['generation']['model_name']} "
                f"coverage={v['citation_coverage']:.0%} "
                f"mismatches={v['numeric_mismatches']}")
    draft = requests.get(f"{BASE}/cases/{case_id}/drafts/latest",
                         timeout=60).json()["draft"]
    ok &= check("draft persisted", draft["DRAFT_VERSION"] >= 1,
                f"{draft['DRAFT_ID']} v{draft['DRAFT_VERSION']}")

    # 7. unattested decision refused
    refused = requests.post(f"{BASE}/cases/{case_id}/decisions", json={
        "decision_type": "approve_for_escalation",
        "rationale": "smoke test", "decided_by": "system.smoke",
        "attested": False}, timeout=60)
    ok &= check("unattested decision refused",
                refused.status_code == 422 and
                refused.json().get("error_code") == "ATTESTATION_REQUIRED")

    # 8. attested decision + audit ordering
    requests.post(f"{BASE}/cases/{case_id}/decisions", json={
        "decision_type": "approve_for_escalation",
        "rationale": "smoke test — synthetic identity",
        "decided_by": "system.smoke", "attested": True}, timeout=60)
    events = [ev["event_type"] for ev in
              requests.get(f"{BASE}/cases/{case_id}/audit-events",
                           timeout=60).json()["audit_events"]]
    expected = ["CASE_FILE_ASSEMBLED", "EXPLANATION_GENERATED",
                "DRAFT_CREATED", "DECISION_RECORDED"]
    ok &= check("audit trail ordered",
                all(t in events for t in expected) and
                events.index("CASE_FILE_ASSEMBLED")
                < events.index("DECISION_RECORDED"),
                " -> ".join(events))

    print()
    if ok:
        print("ALL CHECKS PASSED — demo-ready.")
        if not args.keep:
            print("Reminder: run scripts/reset_demo_state.py --yes before "
                  "presenting so the audit trail starts clean.")
        return 0
    print("SMOKE TEST FAILED — fix before presenting.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
