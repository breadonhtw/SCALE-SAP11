# TrustSphere Backend API Contract

Status: **frozen for Track A ↔ Track B integration** (A1 checklist item —
sign off before B1/B2/B4/B5 build against it). Backend implementation lives
in `src/trustsphere/api/`. Run locally with:

```bash
uvicorn trustsphere.api.app:app --reload --port 8000
```

Interactive schema: `GET /docs` (FastAPI auto-generated) once the server is
running. This document is the human-readable summary; the OpenAPI schema is
authoritative for exact field types.

## Conventions

- **Every response includes `"backend"`** — `"local_sqlite_fallback"` or
  `"hana_cloud:TEAM_11_USER"`. Track B must surface this (or gate on it)
  wherever the UI could otherwise imply live HANA execution that isn't
  happening (CLAUDE.md §18/§26). Never hardcode a backend label in the UI.
- **Errors** are always `{"error_code": str, "message": str}` with a stable
  HTTP status:
  - `404 NOT_FOUND` — alert/case/draft doesn't exist.
  - `409 CONFLICT` — an `Idempotency-Key` was reused with a different request
    body.
  - `422` — validation failure. Known `error_code`s: `INVALID_TIMESTAMP`,
    `INVALID_DECISION_TYPE`, `ATTESTATION_REQUIRED`, `INVALID_WORKFLOW_STATUS`,
    plus FastAPI's own request-schema validation errors.
  - `500 INTERNAL_ERROR` — unexpected; the real exception is logged
    server-side only, never returned to the client (CLAUDE.md §13).
- **Idempotency**: any create/start endpoint accepts an optional
  `Idempotency-Key` header. Replaying the same key with the same request body
  returns the exact original response (byte-for-byte, including timestamps).
  Reusing the key with a *different* body is a `409 CONFLICT`. Endpoints that
  support it: `POST /alerts/{id}/score`, `POST /alerts/{id}/predict-sla`,
  `POST /cases/{id}/assemble`, `POST /cases/{id}/decisions`,
  `POST /cases/{id}/review-workflows`.
- **Timestamps**: ISO-8601, UTC. A trailing `Z` is accepted on input.
- **Money**: `AMOUNT_USD` and related fields are always source-verbatim —
  the API never recomputes or rounds a financial amount; only the scoring
  engine's *normalised 0-100 factor value* is a derived number.

## Alerts

### `GET /health`
Repository health check. `{"backend", "ok", "alert_count", ...}`.

### `GET /alerts?status&limit&offset`
Raw alert list, unscored/unordered. `{"backend", "count", "alerts": [AlertSummary, ...]}`.

### `GET /alerts/queue?limit`
**The ranked investigator queue.** Only alerts that have (a) been scored via
`POST /alerts/{id}/score` and (b) are not in a `CLOSED_*` status. Ordered
per CLAUDE.md §9 "Queue policy": hard override first, then urgency tier,
then SLA time remaining, then urgency score, complexity only as a tie-break
signal exposed in the row (never sorted on directly).
`{"backend", "count", "queue": [{ALERT_ID, ALERT_TYPE, STATUS, SLA_DUE_AT, URGENCY_SCORE, URGENCY_TIER, HARD_OVERRIDE_CODE, COMPLEXITY_BAND, COMPLEXITY_POINTS, CALCULATED_AT}, ...]}`.

### `GET /alerts/{alert_id}`
`{"backend", "alert": AlertSummary, "priority_score": ScoreResult | null, "predictive_sla": dict | null}`.
`priority_score`/`predictive_sla` are `null` until scored/predicted at least once.

### `POST /alerts/{alert_id}/score`
Body: `{"as_of": "<ISO-8601>?"}` (optional, defaults to now).
Runs the deterministic urgency engine (CLAUDE.md §9) and persists the result.
`{"backend", "score": ScoreResult}` — see `ScoreResult` shape below.

### `POST /alerts/score-all`
Batch-scores every alert currently in the alert table. `{"backend", "scored_count"}`.
No idempotency key (safe to re-run; scores are replaced, not appended).

### `POST /alerts/{alert_id}/predict-sla`
Body: `{"as_of": "<ISO-8601>?"}`. Runs the advisory SLA-margin model (A4;
CLAUDE.md §10 — **advisory/shadow mode only, never mixed into urgency**).
`{"backend", "prediction": PredictionResult}`.

`PredictionResult`:
```json
{
  "prediction_type": "expected_resolution_hours",
  "prediction_value": 1617.4,
  "model_name": "heuristic_sla_margin",
  "model_version": "heuristic-v1",
  "feature_snapshot_id": "...",
  "advisory_only": true,
  "label": "Advisory — pilot/shadow mode",
  "extra": {"...": "model-specific diagnostics"}
}
```
Track B **must** render `label` verbatim next to any use of `prediction_value`.

`ScoreResult`:
```json
{
  "alert_id": "ALERT-9001",
  "urgency_score": 96.4,
  "urgency_tier": "CRITICAL",
  "hard_override": {"code": "SANCTIONS_MATCH", "forced_tier": "CRITICAL", "reason": "..."},
  "factors": [{"factor_code": "typology_severity", "raw_value": "CRITICAL", "normalised_value": 100.0, "weight": 0.25, "weighted_points": 25.0, "reason_code": "...", "policy_version": "2026-07-30.1"}, "... 4 more"],
  "complexity_band": "HIGH",
  "complexity_points": 11,
  "policy_version": "2026-07-30.1",
  "calculated_at": "2026-07-30T14:07:29Z",
  "caveats": ["unresolved: ..."]
}
```
`hard_override` is `null` when none fired — the `factors` breakdown is always
present regardless (CLAUDE.md §9: an override "does not erase the underlying
factor breakdown").

## Cases

### `POST /alerts/{alert_id}/cases`
Body: `{"assigned_team": "financial-crime-ops", "region": "APJ"}` (both have
defaults). Idempotent per `alert_id` — calling twice returns the same case.
`{"backend", "case": {CASE_ID, ALERT_ID, ASSIGNED_TEAM, STATUS, CREATED_AT, UPDATED_AT, REGION}}`.

### `GET /cases/{case_id}`
The one shared-state read Streamlit and the agent both use (CLAUDE.md §6
Joule-to-Streamlit proof):
`{"backend", "case", "case_file": CaseFile | null, "latest_draft": dict | null, "workflow": WorkflowInstance | null, "decisions": [Decision, ...]}`.

### `POST /cases/{case_id}/assemble`
Runs HybridRAG (CLAUDE.md §11) and persists the typed `CaseFile`.
`{"backend", "case_file": CaseFile}` — full shape documented in
`src/trustsphere/domain/cases.py` (`CaseFile`), sections: `alert_details`,
`priority_explanation`, `predictive_advisories`, `customer_profile`,
`counterparty_profiles`, `transaction_timeline`, `entity_relationships`,
`related_alerts`, `policy_context`, `historical_case_references`,
`missing_information`, `source_provenance` (citations — every factual claim
Track B generates must cite one of these `citation_id`s), `data_freshness`,
`source_coverage`.

### `POST /cases/{case_id}/drafts` — **Track B's generation endpoint calls this to persist**
Body (`DraftRequest`):
```json
{"content": "...", "generation_id": "gen-123", "prompt_version": "v1", "model_version": "claude-...", "created_by_type": "agent|human", "verification_status": "unverified"}
```
This is the persistence half of `POST /cases/{id}/explanations` /
`POST /cases/{id}/drafts` from CLAUDE.md §13 — **Track B owns the actual
generation call** (AI Core orchestration, citation/numeric validation) and
calls this endpoint once it has a validated narrative. Track A does not
generate text.
`{"backend", "draft": {DRAFT_ID, CASE_ID, DRAFT_VERSION, CONTENT, ...}}`.
`DRAFT_VERSION` auto-increments per case.

### `GET /cases/{case_id}/drafts/latest`
`{"backend", "draft": {...}}` or `404` if no draft yet.

### `POST /cases/{case_id}/decisions`
Body (`DecisionRequest`):
```json
{"decision_type": "approve_for_escalation|return_for_edit|request_information", "rationale": "non-empty", "decided_by": "human identity", "attested": true}
```
**`attested` must be `true`** — CLAUDE.md §14 "only a human decides"; an
unattested decision is refused with `422 ATTESTATION_REQUIRED`, not silently
recorded. `{"backend", "decision": Decision}`.

### `GET /cases/{case_id}/decisions`
`{"backend", "count", "decisions": [Decision, ...]}`.

### `GET /cases/{case_id}/audit-events`
Full append-only audit trail for the case, oldest first.
`{"backend", "count", "audit_events": [AuditEvent, ...]}`. Event types:
`ALERT_SCORED`, `SLA_PREDICTED`, `CASE_FILE_ASSEMBLED`,
`EXPLANATION_GENERATED`, `DRAFT_CREATED`, `DRAFT_UPDATED`,
`WORKFLOW_STARTED`, `WORKFLOW_TRANSITIONED`, `DECISION_RECORDED`.

### `POST /cases/{case_id}/review-workflows` — **B5's SBPA trigger target**
Body (`ReviewWorkflowRequest`): `{"draft_id": "...?", "senior_review": false}`.
Starts one workflow instance. `is_fallback` is set honestly at creation from
`Settings.workflow_backend` / `SAP_BUILD_API_BASE_URL` — `true` unless SBPA
is actually configured. `{"backend", "workflow": WorkflowInstance}`.

### `PATCH /cases/{case_id}/review-workflows` — **B5's SBPA callback target**
Body (`ReviewWorkflowTransitionRequest`): `{"status": "PENDING|IN_REVIEW|SENIOR_REVIEW|APPROVED|RETURNED|INFO_REQUESTED", "external_instance_id": "...?"}`.
Updates the case's latest workflow instance. Supplying `external_instance_id`
is what flips `is_fallback` to `false` — this is the one place in the API
that proves a live SBPA instance actually ran, so B5's integration must pass
the real SBPA instance ID here, not a placeholder.
`{"backend", "workflow": WorkflowInstance}`.

`WorkflowInstance`:
```json
{"workflow_id": "...", "case_id": "...", "external_instance_id": "SBPA-INST-1", "status": "APPROVED", "started_at": "...", "completed_at": "...", "is_fallback": false}
```

## Ownership boundary (who builds what)

| Concern | Owner | Notes |
|---|---|---|
| All endpoints above | Track A | Implemented, tested (`tests/`) |
| `POST /cases/{id}/explanations` (cited explanation generation) | Track B | Calls AI Core orchestration, validates citations/numbers, then calls `POST /cases/{id}/drafts` here to persist |
| Investigation Assistant tool-calling loop | Track B | Tools wrap these same endpoints — `GetAlertDetails`→`GET /alerts/{id}`, `CalculateRegulatoryUrgency`→`POST /alerts/{id}/score`, `RetrievePolicyContext`→ case file's `policy_context` (via assemble), `DraftSupportingNarrative`→ B's generation + `POST /cases/{id}/drafts`, `SaveDraft`→`POST /cases/{id}/drafts`, `StartHumanReview`→`POST /cases/{id}/review-workflows` |
| SAP Build Process Automation wiring | Track B (B5) | Calls `POST` to start, tenant SBPA calls back to `PATCH` on completion/transition |
| Streamlit cockpit | Track B (B1/B3) | Reads/writes exclusively through this API — no direct DB access |

## Schema reference

Table definitions: `migrations/001_app_schema.sql` (HANA) /
`src/trustsphere/persistence/local_schema.sql` (local fallback, same
app-state shape). Domain/pydantic types: `src/trustsphere/domain/`.

## Change process

This contract is frozen after sign-off. A change requires a short sync
between Track A and Track B, not a solo edit — update this file and the
corresponding router/schema in the same change.
