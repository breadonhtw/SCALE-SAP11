# Team handover — 2026-07-31 (presentation day)

This doc is the complete state of the technical build + what remains today. Everything below
is on `main` (pushed) and verified by `scripts/run_demo_checks.py`
(**ALL CHECKS PASSED** on live HANA, 2026-07-31 early morning).

## TL;DR

The prototype is **feature-complete and live against the real tenant**:
HANA Cloud system of record (1,554 open alerts scored), HybridRAG CaseFiles
(SQL + graph + in-DB vector), cited generation on SAP AI Core orchestration
(gpt-4.1-mini, content filtering active), bounded Investigation Assistant,
attestation-gated decisions with append-only audit, and a live SBPA
human-review workflow in the SAP Build lobby. 77 tests green. What remains
is presentation logistics, not engineering.

## What was built (proof: docs/capability-matrix.md, CLAUDE.md §0.1)

| Piece | State |
|---|---|
| Track A backend (API, scoring, HybridRAG, persistence) | Merged; frozen contract `docs/api-contract.md` |
| HANA go-live | App schema + policy corpus loaded; all 1,554 open alerts scored; `/health` = `hana_cloud:TEAM_11_USER` |
| Generation (B2) | Orchestration v2 REST, **gpt-4.1-mini live-verified**, Azure CS + Llama Guard filtering active, prompts v1.1, citation/numeric validation (recent runs 90–100% coverage, 0 mismatches) |
| Cockpit (B1/B3) | Paginated queue (32 pages), alert detail, CaseFile tabs, narrative page, Review & Decide (server-enforced attestation), decided-chips, SGT timestamps |
| Assistant (B4) | 5-tool loop on orchestration tool-calling; refuses dismiss/file/block/decide; shared-state proof works |
| SBPA (B5) | Process **live in SAP Build lobby** (SCALE 2026 env, My Inbox approval demonstrated). API trigger coded + unit-tested but **dormant: needs a service key** (see below) |
| Demo hardening | `run_demo_checks.py` (9-point smoke), `reset_demo_state.py`, `docs/demo-script.md` (full 7-min runbook + Q&A answers) |

**Person A — please review these diffs (integration fixes I made in Track A
files during HANA go-live):** `domain/alerts.py|cases.py|citations.py`
(INTEGER→str id coercion — tenant ids are ints), `persistence/hana.py|local.py|base.py`
(`update_case_status`, queue LEFT JOIN for decided-chips, pagination offset,
`count_scored_open_alerts`), `api/routers/cases.py` (decision → case status,
SBPA wiring, `/review-workflows/sync`).

## What needs to happen TODAY

1. **Set up the demo machine** (15 min — see "Run it" below). Everything
   state-ful lives in HANA, so any laptop with the team credentials works.
2. **Dry run ×2** following `docs/demo-script.md` with a timer.
3. Right before presenting: `python scripts/run_demo_checks.py` then
   `python scripts/reset_demo_state.py --yes`.
4. **Workflow demo path (updated 31 Jul ~12:20):** organisers ruled that
   SAP Build is **not available for team use** — do NOT demo the SBPA lobby
   process or My Inbox. The demo uses the cockpit's **local review-state
   machine** (the documented §18 fallback, already the default): Start
   human review → honest fallback label → attested decision completes the
   workflow → audit trail. Say it as: "the human-review workflow runs on a
   local state machine matching the SAP Build contract; the SBPA
   integration code is built and tested, pending tenant access." The
   dormant SBPA client stays in the repo as target-state evidence.

## Run it (any machine)

```
git clone https://github.com/breadonhtw/SCALE-SAP11 && cd SCALE-SAP11
pip install -e ".[dev]"
```

Create `.env` in the repo root (values are in the team credentials bundle —
the `team-11.zip` shared in the team chat; DO NOT commit):

```
DATA_BACKEND=hana
TEAM11_CREDS=<absolute path to team_11_credentials.json>
TRUSTSPHERE_GEN_MODEL=gpt-4.1-mini
```

Then two terminals:

```
python -m uvicorn trustsphere.api.app:app --port 8000
python -m streamlit run streamlit_app/app.py --server.port 8501
```

Cockpit: http://localhost:8501 (expect the green `hana_cloud:TEAM_11_USER`
banner). Scores for all 1,554 alerts are already in HANA — nothing to
re-run. Full flow check: `python scripts/run_demo_checks.py`.

## Honesty lines to keep straight on stage (CLAUDE.md §26)

- The assistant is a **custom agent on SAP AI Core orchestration** —
  never call it Joule (Joule Studio is unavailable in this tenant).
- Predictive SLA output is **advisory/shadow mode**, local open-source
  model (PAL/APL not in tenant), never inside the urgency score.
- Workflow: **local review-state machine fallback, clearly labelled** —
  never claim live SAP Build Process Automation (organisers ruled it out of
  team scope; the integration code exists as target-state only).
- The prototype **never closes alerts** — it records attested hand-off
  decisions; closure is downstream case management.
- The dataset's every open alert is already SLA-breached — that's the
  case-study aged backlog, narrate it as such.
