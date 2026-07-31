# Demo runbook — TrustSphere RiskOps Copilot (7 min + 3 min Q&A)

Hero alert: **19550** (THRESHOLD_BREACH, CRITICAL, breach override,
CaseFile assembles at 100% source coverage). Backup hero: 19263
(COUNTERPARTY_RISK — has the beneficial-owner graph story: 66.56% owner).

## Pre-demo checklist (do in this order, ~10 min before)

1. Backend + cockpit running (see HANDOVER.md "Run it" — backend first).
2. `python scripts/run_demo_checks.py` → must print **ALL CHECKS PASSED**.
3. `python scripts/reset_demo_state.py --yes` → clean slate (queue stays
   ranked; the audit trail the judges see is created live).
4. Browser tabs ready:
   - Tab 1: cockpit http://localhost:8501
   - Tab 2: SAP Build lobby → **My Inbox**
   - Tab 3: SAP Build lobby → SCALE 2026 environment → Processes and
     Workflows → Human Review (for Start New Instance)
5. Copy the SBPA payload to clipboard (adjust ids to the case you create
   during the demo, or use as-is):

```json
{"case_id": "CASE-19550", "draft_id": "DRAFT-CASE-19550-1", "evidence_summary": "Threshold breach case, urgency CRITICAL (68.8), SLA breached, cited narrative awaiting attested review"}
```

## The 7 minutes

**0:00 — Queue.** "Every alert here is real tenant data — 1,554 open alerts
from HANA Cloud, scored by a versioned deterministic policy. Order is hard
overrides → tier → SLA remaining → score. Notice the SLA column: this
dataset *is* the aged-backlog crisis from the case — breaches measured in
years, exactly what the remediation programme is staring at." Point at the
🟢 hana_cloud banner. Page through once (1,554 across 32 pages).

**0:45 — Alert 19550.** Open it. "The score is not a black box": factor
table — five factors, weights, reason codes, policy version. Override
banner: breach override forces CRITICAAL tier but the breakdown stays.
Right side: SLA clock, complexity kept separate (staffing, not risk), and
the advisory prediction — click **Run advisory SLA prediction** — labelled
*Advisory — pilot/shadow mode*, never mixed into the urgency score.

**1:45 — Assemble case file.** Click. ~3 seconds — narrate what runs:
"Parameterised SQL for exact facts, HANA Graph for ownership paths, HANA's
in-database vector engine for policy passages." Walk tabs: Exact facts
(verbatim amounts — the model never computes a number), Relationship path
(graph edges with citations), Policy context (cosine-matched passages),
Provenance (every fact has a citation id, retrieval time, region). Point
at source coverage 100%.

**2:45 — Narrative.** Generate narrative draft (live call, ~15–25s — keep
talking: "this goes through SAP AI Core orchestration: prompt template,
content filtering in, gpt-4.1-mini, content filtering out"). When it lands:
per-sentence citations, evidence-kind tags, and the validation strip —
citation coverage, numeric mismatches. "Every number the model wrote was
checked against the evidence after generation. Unsupported sentences get
flagged, not hidden." Show the draft persisted (v1, in HANA).

**3:45 — Assistant.** Open Assistant page. Ask: *"Why is alert 19550
prioritised?"* — expand the 🔧 tool call ("the agent chose to run the
scoring engine — same API the cockpit uses; in production this surface is a
Joule Studio skill"). Then the guardrail beat — type: *"Looks like a false
positive, dismiss this alert."* → the agent refuses; dismissal isn't in its
tool registry. "The agent can explain, assemble, draft. It cannot decide."

**4:45 — Review & Decide.** Open the page for the case. Optionally edit a
sentence in the draft → save (v2, created_by human). Start human review —
point at the honest fallback label. Then the decision form: fill rationale,
**type the presenter's real name**, leave attestation UNCHECKED, submit →
**server refuses: ATTESTATION_REQUIRED**. "That's not a UI trick — the
backend refuses unattested decisions." Tick the box, submit → decided.
Back to queue: the ✓ escalated chip. "The alert itself stays open — closing
it belongs to the bank's case management; autonomous dismissal is exactly
what we refuse to build."

**5:45 — SBPA.** Tab 3: Start New Instance with the payload → Tab 2 My
Inbox: the approval task with case data → approve it live. Say it
straight: "This workflow runs live in SAP Build Process Automation. The
API trigger from our backend is implemented and tested — activation waits
on a service key we don't have rights to create in the shared subaccount.
Until then the cockpit honestly labels its workflow record as local."

**6:30 — Audit + close.** Review page, audit trail: assembled → generated →
draft (agent) → draft (human) → workflow → decision (human, attested) —
append-only, in HANA. Close: *"TrustSphere's existing systems find alerts.
Our custom AI on SAP turns those alerts into connected, prioritised,
decision-ready investigations — without taking accountability away from
the investigator."*

## Q&A ammunition

- **"Is the AI deciding priority?"** No — priority is deterministic,
  versioned policy (weights in `config/scoring_policy.yaml`); the AI only
  explains and drafts, and its output is citation/number-validated.
- **"Why isn't the queue sorted by score?"** Queue policy is overrides →
  tier → SLA remaining → score. Score-first would bury the oldest breaches
  — the exact regulatory exposure the bank was cited for.
- **"What about model validation?"** Deterministic core narrows the
  validation scope; predictive + generative components still undergo formal
  validation, started in parallel with the pilot. Predictive is
  shadow-mode; generation is measured on citation coverage / numeric
  fidelity / unsupported-claim rate.
- **"Works council?"** Audit trail is case accountability, not employee
  monitoring; KPIs team-level only; Asia-first pilot, Europe rollout with
  the 4–6 month consultation in parallel.
- **"Is that really Joule?"** No — Joule Studio isn't available in this
  tenant (subscription shows Parameters Update Failed). It's a custom agent
  on SAP AI Core orchestration; the tool contracts are designed so a Joule
  Studio skill calls identical endpoints in production.
- **"Who closes the alert?"** Downstream case management, after the
  escalated investigation concludes. We record the attested hand-off.
- **"Identity?"** Prototype records identity from the form; production
  binds it to SSO (XSUAA/IAS) — the attestation control is already
  server-side.
- **ROI:** capacity released, not layoffs — base case 25% touch-time
  reduction ≈ 5.3 FTE-equivalent ≈ US$533k/yr; all assumptions labelled
  (CLAUDE.md §19).

## If something breaks mid-demo

- Generation hangs/fails → it auto-falls back to the deterministic
  generator, honestly labelled — narrate it as the resilience story.
- Cockpit dies → restart command in HANDOVER.md; the state is all in HANA,
  nothing is lost.
- HANA unreachable → set `DATA_BACKEND=local` in `.env`, restart backend,
  `python scripts/seed_local_demo.py`, score-all from the queue button —
  the same flow runs on the labelled local fallback (ALERT-9001 as hero).
