# Team handover — 2026-07-31 (presentation day)

Brandon (Track B) is unwell and may miss the presentation. This doc is the
complete state of the technical build + what remains today. Everything below
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

1. **Submission email — BEFORE 2:30 PM** to john.gao01@sap.com, identify as
   team 11: slide deck (business owner), codebase (GitHub link
   https://github.com/breadonhtw/SCALE-SAP11 or zip of `main`), artifacts:
   `sap/orchestration/trustsphere-narrative-config.json` (team-authored
   orchestration config), screenshots (My Inbox approval task, AI Launchpad
   model selection). **This is the hard deadline.**
2. **Set up the demo machine** (15 min — see "Run it" below). Everything
   state-ful lives in HANA, so any laptop with the team credentials works.
3. **Dry run ×2** following `docs/demo-script.md` with a timer.
4. Right before presenting: `python scripts/run_demo_checks.py` then
   `python scripts/reset_demo_state.py --yes`.
5. *(Optional, 5-min unlock)* **SBPA service key**: Person A (or an
   organiser with subaccount rights) creates a service key on the
   `star-sap-build` instance (BTP cockpit → Instances → ⋯ → Create Service
   Key), save JSON to `Desktop/SAP/team-11/sbpa_service_key.json`, add
   `SBPA_SERVICE_KEY=<that path>` to `.env`, restart backend → the cockpit's
   "Start human review" goes fully live (real instance id,
   `is_fallback:false`, `/sync` pulls the My Inbox outcome). Brandon's user
   was permission-blocked; api-key-only auth is proven insufficient (all
   variants 401 — capability matrix has the trail). If no key: demo the
   lobby-triggered flow per the runbook — it's honest and works.

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
- SBPA: live in the lobby, human-triggered; **API trigger implemented but
  awaiting a service key** — say it exactly like that.
- The prototype **never closes alerts** — it records attested hand-off
  decisions; closure is downstream case management.
- The dataset's every open alert is already SLA-breached — that's the
  case-study aged backlog, narrate it as such.

Brandon is reachable async in the team chat. Get the deck in before 2:30.
