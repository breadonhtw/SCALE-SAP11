# Two-Person Technical Workplan — SCALE 2026 Team 11

Scope: the full technical build (prototype + SAP integrations + demo).
The slide deck / business case is handled separately and is NOT in this split.

**Interface contract (agree first, then work independently):**
- REST API shapes in `docs/api-contract.md` — frozen after both sign off.
- HANA schema `TEAM_11_USER` — cleaned tables exist; app tables (cases,
  drafts, decisions, audit) defined in A1 and frozen.
- Any contract change requires a 2-minute sync, not a solo edit.

---

## Track A — Data, Scoring & Retrieval (Person A)

Owns HANA, the deterministic core, and evidence assembly.

### A1. Backend skeleton  ~half day
- [ ] Repo scaffolding, `pyproject.toml`, config loading, `.env.example`
- [ ] FastAPI app with `/health`, `/alerts`, `/alerts/{id}`
- [ ] HANA persistence module (parameterised SQL, connection from creds file)
- [ ] App-state tables in HANA: `CASES`, `NARRATIVE_DRAFTS`, `DECISIONS`,
      `WORKFLOW_INSTANCES`, `AUDIT_EVENTS` (append-only)
- [ ] Publish `docs/api-contract.md` → **sign-off checkpoint with B**

### A2. Deterministic urgency engine  ~half day
- [ ] `config/scoring_policy.yaml` (versioned weights, tiers, overrides)
- [ ] Pure scoring engine: 5 factors, reason codes, hard overrides,
      separate complexity band
- [ ] `POST /alerts/{id}/score` + batch scorer persisting to HANA
- [ ] Unit tests: factors, tier boundaries, overrides, queue ordering

### A3. CaseFile + HybridRAG  ~1 day
- [ ] Typed `CaseFile` assembly with citations, freshness, missing-info
- [ ] HANA Graph workspace over company/owner/counterparty edges +
      relationship-path retrieval
- [ ] Policy/rule embedding into HANA vector (in-DB NEB) + semantic retrieval
- [ ] `POST /cases/{id}/assemble`, `GET /cases/{id}`

### A4. SLA advisory model  ~half day
- [ ] Local open-source model, remaining-margin framing (99.4% breach rate
      makes breach classification useless — see data-quality report)
- [ ] Behind `SLAPredictor` interface; heuristic fallback; honest labelling
- [ ] Scores persisted to HANA with model version; `POST /alerts/{id}/predict-sla`

### A5. Decision + audit endpoints  ~half day
- [ ] `POST /cases/{id}/decisions` (escalate / return / request-info,
      attestation required), rationale persisted
- [ ] Append-only `AUDIT_EVENTS` writer + `GET /cases/{id}/audit-events`
- [ ] Idempotency keys on create/start operations

---

## Track B — AI Surfaces, Cockpit & Workflow (Person B)

Owns everything that talks to the model or the judge's eyes.

### B1. Streamlit cockpit core  ~1 day  (starts against mocked API from the contract)
- [ ] Ranked queue view (tier badges, SLA countdown, override markers)
- [ ] Alert detail: factor breakdown, complexity, advisory SLA panel
      ("Advisory — shadow mode" labelling)
- [ ] CaseFile view: exact facts vs AI synthesis distinction, citations,
      freshness, missing-info warnings
- [ ] Relationship-path visual (graph payload shape from the contract)

### B2. Generation via AI Core orchestration  ~1 day
- [ ] Orchestration client; verify + record which model responds
      (update `docs/capability-matrix.md` with proof)
- [ ] Cited explanation + narrative endpoints; citation/number validation;
      unsupported-claim flagging
- [ ] Draft persistence with versions (via A1's draft tables/endpoints)
- [ ] Tests: citation coverage, numeric fidelity

### B3. Decision + audit UX  ~half day
- [ ] Narrative editor with "AI-generated draft — investigator verification
      required" labelling, edit + attest controls
- [ ] Decision actions wired to A5 endpoints
- [ ] Audit-history panel

### B4. Investigation Assistant  ~half day
- [ ] Orchestration tool-calling loop over 4–5 backend tools
      (GetAlertDetails, CalculateRegulatoryUrgency, RetrievePolicyContext,
      DraftSupportingNarrative, SaveDraft)
- [ ] Guardrails: no dismiss/file/block actions exposed
- [ ] Chat panel in the cockpit; labelled "custom agent on SAP AI Core
      orchestration; production surface: Joule Studio" — never "Joule"

### B5. SAP Build Process Automation  ~half day (tenant work, can slot anywhere)
- [ ] Human-review workflow in SBPA (receive case+draft id, review task,
      attestation, approve/return/request-info, senior route)
- [ ] Wire trigger + callback to A5's endpoints
- [ ] If SBPA fights back: fallback local review-state machine, labelled

---

## Joint — Demo hardening  (final half day, both)

- [ ] Smoke-test command covering: queue loads, hero case scores
      deterministically, CaseFile returns citations, prediction + draft
      return (real or fallback), draft persists, decision appends audit event
- [ ] Demo script: hero alert + one contrasting alert + held-out case
- [ ] Dry run ×2 with timer (7 min + 3 min Q&A)
- [ ] Submission to john.gao01@sap.com **before 2:30 PM**: deck (from the
      business owner), codebase, artifacts — identify as team 11

---

## Sequencing / dependency notes

1. **Day-start together (~30 min):** freeze API contract + schema (A1
   output). Everything else runs parallel.
2. B1 starts immediately against mocked JSON shaped by the contract; swaps
   to live endpoints as A2/A3 land. B2 needs only A1's draft tables.
3. Hard cross-dependencies (all resolved by the contract, not by waiting):
   - B1 CaseFile view ← A3 payload shape
   - B3 decision UX ← A5 endpoints
   - B4 assistant tools ← A2/A3/A5 endpoints live
4. **Stop rule if time collapses:** A2 + B1 alone are a complete honest
   demo core (deterministic scoring + working cockpit). Cut order:
   B5 → its fallback, then B4, then A4 → heuristic, then B2 → prepared
   fallback text. Never cut A2, A3-SQL-facts, B1, or the audit trail.
