# TrustSphere Hybrid AI Risk Intelligence Assistant

This file is the implementation contract for Claude Code working on the TrustSphere SCALE 2026 hackathon project.

The goal is to build a small, reliable, SAP-native prototype that turns existing financial-crime alerts into prioritised, evidence-backed, decision-ready cases. Do not build a new transaction-monitoring or crime-detection model. Do not automate regulated decisions. Do not add components merely to increase the number of SAP product names in the architecture.

## 0. Tenant reality addendum (verified 2026-07-30)

This section records what the team-11 tenant actually provides (proof: `scripts/check_capabilities.py`, results in `docs/capability-matrix.md`). Where it conflicts with aspirational text below, this section wins.

**Verified available:**

- HANA Cloud (Singapore), user `TEAM_11_USER`; read-only source schema `TRUSTSPHERE_REFERENCE`, writable team schema `TEAM_11_USER`.
- HANA vector engine (`REAL_VECTOR`, `COSINE_SIMILARITY`) **and** in-database `VECTOR_EMBEDDING` (SAP NEB model) — semantic retrieval is fully HANA-native.
- HANA Graph property-graph workspaces (create/drop verified) — the relationship layer.
- SAP AI Core generative AI hub with a RUNNING `orchestration` deployment (resource group `team-11`); tenant model library lists Amazon, Anthropic, Cohere, Google, Mistral AI, NVIDIA, OpenAI, Perplexity, SAP providers. Verify the concrete model with a live call before claiming it.
- SAP Build Process Automation entitlement (user-confirmed in tenant UI).

**Verified unavailable (fallbacks are binding):**

- **Joule Studio** — not available in the tenant. Replacement: a custom **Investigation Assistant** chat in the cockpit using AI Core orchestration tool-calling over the same backend endpoints Joule skills would have used (`GetAlertDetails`, `CalculateRegulatoryUrgency`, `RetrievePolicyContext`, `AssembleCaseFile`, `DraftSupportingNarrative`, `SaveDraft`, `StartHumanReview`). Label it "custom agent on SAP AI Core orchestration; production surface: Joule Studio". Never label it as Joule.
- **SPARQL/triple store** — no active TripleStore in landscape. Use HANA Graph workspaces; never claim SPARQL/RDF.
- **PAL/APL** — no `_SYS_AFL` PAL procedures, no AFL role granted. The SLA-risk advisory model is built locally with open-source tooling (permitted by case rules: non-SAP software must be open-source/free tier) behind the `SLAPredictor` interface, scores persisted to HANA with model version; presented as an advisory demonstration, production target PAL/APL or an approved model service.
- **SAP-RPT** — not exposed; not used.

**Data reality:** the tenant ships a provisioned dataset (150k transactions, 5k companies, 12.5k beneficial owners, 5k risk alerts, 500 compliance cases, sanctions/screening/country reference tables). It was profiled and cleaned into `TEAM_11_USER` (`scripts/clean_data.py`, findings in `docs/data-quality-report.md`). References below to seeding synthetic data are superseded — build against the cleaned snapshot. Key cleaning facts: use `RISK_ALERTS.SLA_BREACHED_DERIVED` (not the source flag) as any SLA label; 99.4% of closed alerts breached SLA, so the predictive layer targets remaining-margin/time-to-breach, not breach classification; `COMPANIES.KYC_EFFECTIVE_STATUS` is the trustworthy KYC field; `LEGAL_NAME` is not unique — key on `REGISTRATION_NUMBER`/`LEI_CODE`; 25 alerts carry `ALERT_DQ_FLAG='RESOLVED_BEFORE_CREATED'` and are excluded from duration training.

## 0.1 Build status addendum (verified 2026-07-31)

Where this section conflicts with the plans below, this section records what
was actually built and verified. Proof lives in `docs/capability-matrix.md`
(live-call evidence) and the git history on `track-b/b1-cockpit`.

**Delivered and live (all against the real tenant):**

- **Track A backend merged** (`src/trustsphere/`): FastAPI service, HANA +
  local-SQLite repositories, deterministic scoring engine, HybridRAG
  assembly, heuristic SLA advisory. Contract frozen in
  `docs/api-contract.md` (with Track B addendums: `/explanations`,
  `/review-workflows/sync`).
- **HANA Cloud is the live system of record**: app-state tables created via
  `scripts/init_app_schema.py`; policy corpus embedded in-database
  (`scripts/load_policy_corpus.py`, NEB `VECTOR_EMBEDDING` +
  `COSINE_SIMILARITY` verified live); **all 1,554 open alerts scored**;
  CaseFile assembly runs SQL + graph-derived owners + vector policy
  retrieval on real data (~3s). `DATA_BACKEND=hana` in `.env`;
  `/health` reports `hana_cloud:TEAM_11_USER`.
- **Generation (B2)**: SAP AI Core orchestration v2 via the documented REST
  API (`src/trustsphere/generation/`), model **`gpt-4.1-mini`**
  (team-selected in AI Launchpad, live-verified), **content filtering
  active** (Azure Content Safety + Llama Guard 3 8B, mirrored from the
  team-authored portal export in `sap/orchestration/`; portal cannot save
  orchestration configs in this tenant release — the exported JSON is the
  governed artifact). Prompts versioned (`config/prompts/*-1.1.md`,
  verbatim-dates rule); post-generation citation-coverage and
  numeric-fidelity validation; recent live runs 100% coverage. Deterministic
  fallback preserves the contract shape.
- **Cockpit (B1/B3)**: paginated ranked queue (50/page over all 1,554),
  alert detail with factor breakdown, CaseFile tabs, narrative page with
  per-sentence citations + validation flags, Review & Decide with
  server-enforced attestation (`422 ATTESTATION_REQUIRED`), decided-state
  chips (case status `ESCALATED`/`RETURNED_FOR_EDIT`/`INFO_REQUESTED` —
  the alert row itself is never closed by this prototype), append-only
  audit views. Timestamps stored UTC, rendered SGT. Tables render as
  markdown (pandas DLLs are blocked by the dev machine's Application
  Control policy — also why the SDK is not used for generation).
- **Investigation Assistant (B4)**: client-side tool-calling loop on
  orchestration v2 (`src/trustsphere/assistant/`), five bounded tools over
  the same API; refuses dismiss/file/block/decide; live-verified including
  the shared-state proof (agent-created draft visible in the cockpit).
- **SBPA (B5) — hybrid**: process "TrustSphere Human Review" is **live in
  the SAP Build lobby** (SCALE 2026 environment; API trigger, approval
  form, My Inbox task executed by the user). The **API-trigger path is
  blocked** — no service-management rights in the shared subaccount, and
  api-key-only auth was tested to exhaustion (all variants 401). Backend
  trigger + polling `/sync` code (`src/trustsphere/workflows/sbpa.py`) is
  implemented, unit-tested, and dormant: setting `SBPA_SERVICE_KEY`
  activates it (trigger URL + definitionId already configured in `.env`).
  The cockpit keeps the honest local-fallback label meanwhile.
- **Tests**: 77 passing (scoring, review flow incl. attestation refusal,
  generation validation, assistant loop guardrails, SBPA client).
- Integration fixes applied to Track A code during HANA go-live (int→str
  id coercion in `domain/*` via `coerce_numbers_to_str`; queue
  LEFT JOIN for case status; `update_case_status`) — Person A should
  review these diffs on merge.

**Data reality notes:** entity ids in the tenant snapshot are INTEGERs;
every open alert in the provisioned dataset is already past SLA (dates
2024–2025), so the `IMMINENT_SLA_BREACH` override fires queue-wide — the
dataset is the aged-backlog crisis, which the demo narrates as such.

**Remaining before submission:** demo-state reset + smoke-test script,
demo runbook (`docs/demo-script.md`), branch push + merge with Person A,
timed dry runs. Governance note: app-state test artifacts in HANA may be
wiped as disclosed dev hygiene before the demo; production audit rows are
append-only.

## 1. Product definition

### One-line pitch

TrustSphere RiskOps Copilot helps investigators resolve the highest-risk alerts faster by combining transparent regulatory prioritisation, predictive operational-risk signals, HybridRAG-grounded investigation support, and human-controlled workflow.

### Core product promise

Existing AML systems continue to generate alerts. This product sits between alert generation and the investigator's final decision. It:

1. Ranks alerts by the regulatory urgency of leaving them unresolved.
2. Estimates operational risk such as likely SLA breach and case complexity.
3. Assembles exact facts, entity relationships, and relevant policy context.
4. Produces cited explanations and a supporting investigation narrative.
5. Routes every regulated action through a human review step.
6. Persists shared case state and a complete audit history in HANA Cloud.

### Intelligence boundaries

Keep the layers separate:

| Layer | Question | Implementation | Decision status |
|---|---|---|---|
| Deterministic | Which alert is most urgent under policy? | Versioned rules and hard overrides | May drive queue order after policy approval |
| Predictive | Which case may breach SLA or consume more effort? | SAP HANA ML/PAL/APL or an approved alternative | Advisory/shadow mode in the prototype |
| Retrieval | What exact facts, relationships, and policies apply? | HANA SQL, knowledge graph/SPARQL, and vector search | Evidence only |
| Generative | How can the evidence be clearly explained or drafted? | SAP AI Core generative AI hub through an approved SDK | Draft/support only |
| Human | What action should be taken? | Investigator plus SAP Build Process Automation | Final authority |

Use this formulation in code, UI copy, tests, and presentations:

> Rules determine regulatory urgency. Predictive AI forecasts operational risk. HybridRAG establishes context. Generative AI explains and drafts. Humans decide.

## 2. Case constraints and business baseline

Treat these as case-study inputs unless the source document says otherwise:

- Approximately 12,000 alerts.
- Escalated cases require approximately one to three working days.
- Some payment approvals are delayed by three business days.
- Financial-crime operating costs increased approximately 25%.
- The bank is under a hiring freeze.
- Fourteen client exits were cited.
- The COO target is a 30% reduction in cost per case within 18 months.
- Existing monitoring may have a 90–95% false-positive rate, but reducing that rate is not the prototype target.
- AI-assisted investigation and prioritisation are encouraged, but AI outputs still require formal validation before operational deployment.
- Rule-based prioritisation and workflow changes may follow a lighter governance route than learned detection models.
- Customer personal data must remain in approved regional environments.
- The German Works Council may require a four-to-six-month consultation for tooling that monitors individual employee performance.

If the SCALE 2026 case document becomes available, read it before making material changes and reconcile this section with the source. Never invent missing case facts.

## 3. Scope

### Must build

Build one coherent end-to-end path:

1. A seeded alert queue with at least three alert types.
2. A deterministic regulatory-urgency score with reason codes and hard overrides.
3. A separate investigation-complexity value.
4. One predictive operational-risk output, preferably SLA-breach probability.
5. A structured `CaseFile` assembled from deterministic sources.
6. Hybrid retrieval using exact SQL facts, relationship retrieval, and semantic policy retrieval.
7. One grounded explanation and one supporting narrative draft with citations.
8. A Streamlit investigator cockpit.
9. One bounded custom Investigation Assistant agent (AI Core orchestration tool-calling over the backend endpoints; Joule Studio is unavailable — see §0).
10. One SAP Build Process Automation human-review workflow, if tenant access permits.
11. Shared HANA-backed case and draft state across interfaces.
12. Human decision controls and append-only audit events.
13. Tests for scoring, provenance, permissions, numerical fidelity, and unsupported claims.

### Strong differentiators, only after the core flow works

- A visible entity relationship path backed by graph retrieval.
- A Joule-to-Streamlit shared-state demonstration.
- A working Process Automation approval task.
- Team-level operational metrics.
- A held-out alert showing that retrieval is not hardcoded to the hero case.

### Non-goals

Do not build:

- A new transaction-monitoring or anomaly-detection model.
- A model that predicts whether a customer is criminal, suspicious, or "guilty."
- Autonomous alert dismissal or escalation.
- Autonomous payment blocking.
- Autonomous SAR filing.
- A filing-ready SAR.
- Automatic learning or retraining from every investigator action.
- A global customer database hosted only in Singapore.
- Employee productivity rankings or investigator leaderboards.
- A large enterprise ontology.
- A multi-agent swarm.
- Multiple predictive models when one demonstrable operational prediction is enough.
- A production-grade replacement for the bank's case-management system.
- A custom frontend framework if Streamlit is sufficient.

## 4. Target architecture

```text
Existing AML alerts + KYC + transaction extracts + policy documents
                              |
                              v
                         SAP HANA Cloud
          +-------------------+-------------------+
          |                   |                   |
     SQL tables       Knowledge graph       Vector engine
    exact facts       entity relations      semantic context
          |              SPARQL/query            |
          +-------------------+-------------------+
                              |
                     typed, cited CaseFile
                              |
             +----------------+----------------+
             |                                 |
   deterministic scoring              predictive SLA risk
   rules + hard overrides             hana_ml/PAL/APL
             |                                 |
             +----------------+----------------+
                              |
                    Generative AI Hub
                 explanation + draft only
                              |
            +-----------------+-----------------+
            |                                   |
    Streamlit cockpit          Investigation Assistant agent
                               (AI Core orchestration tools)
            |                                   |
            +---------- shared HANA state ------+
                              |
                SAP Build Process Automation
                    human review/attestation
                              |
                   decision + audit events
```

### Production deployment pattern

The hackathon may use simulated data in a Singapore-region prototype. Production must use a common logical architecture deployed as region-local data planes:

- Asia data plane: region-local HANA, retrieval, model access, and case workflow.
- Europe data plane: region-local HANA, retrieval, model access, and case workflow.
- Other regions: equivalent approved deployments.
- Only approved, aggregated, non-personal operational metrics may move to a group-level dashboard.
- Use residency-aware routing and do not replicate unrestricted personal data across regions.

Do not describe the Singapore prototype as the global production data estate.

## 5. SAP component responsibilities

Every SAP component must have a distinct, working responsibility.

### SAP BTP

- Hosts or connects the backend services in the approved prototype region.
- Provides destination/service-binding patterns where available.
- Is not itself proof that every attached service is enabled.

### SAP HANA Cloud

Use HANA as the shared system of record for:

- Alerts and alert factors.
- Customers, accounts, counterparties, owners, transactions, and jurisdictions.
- Case files and provenance.
- Predictive outputs.
- Narrative drafts.
- Workflow status.
- Investigator decisions.
- Audit events.
- Policy passages and embeddings.
- Graph entities and relationships, where supported.

Use the current HANA Cloud client documentation and tenant-specific connection method. Do not rely on old HANA Express installation guidance for a Cloud deployment.

### HANA SQL

Use parameterised SQL for exact facts:

- Amounts, dates, identifiers, risk ratings, and alert age.
- KYC status and source freshness.
- Related alerts and prior case identifiers.
- Draft, decision, workflow, and audit persistence.

The LLM must never calculate or invent exact financial values.

### HANA knowledge graph and SPARQL

Use a small relationship model to retrieve connected evidence. Confirm the actual graph engine, RDF/SPARQL support, syntax, privileges, and tenant availability before coding against it.

Minimum useful node types:

- Customer
- Account
- Transaction
- Counterparty
- Beneficial owner
- Jurisdiction
- Alert
- Monitoring rule
- Historical case
- Policy document

Minimum useful relationship types:

- `OWNS`
- `CONTROLS`
- `SENT_TO`
- `RECEIVED_FROM`
- `LOCATED_IN`
- `TRIGGERED_BY`
- `RELATED_TO`
- `INVOLVED_IN`
- `GOVERNED_BY`

Keep graph queries narrow and explainable. Return the relationship path and source identifiers, not only prose.

### HANA vector retrieval

Use vector similarity for semantically relevant text such as:

- Policy clauses.
- Rule intent.
- Regulatory guidance.
- Case procedures.
- Permission-filtered historical narratives.

Apply metadata filters before or with similarity search:

- Region.
- Jurisdiction.
- document type.
- permission scope.
- effective date/version.
- language.

Policy and rule-intent content is authoritative. Historical cases are reference material only, must be access-controlled and region-filtered, and must never feed the deterministic urgency score.

### HANA ML, `hana_ml`, PAL/APL, and SAP-RPT

The preferred prototype target is:

> Probability that a case will breach its investigation SLA.

Candidate structured features:

- Alert age and time remaining.
- Regulatory tier.
- Alert category.
- Number of linked entities.
- Number of jurisdictions.
- Missing KYC count.
- Transaction count.
- Related-alert count.
- Historical handling time for the alert category.
- Current team queue load, only if available as an aggregate.

Output examples:

- `sla_breach_probability`
- `expected_duration_hours`
- `complexity_band`
- `model_version`
- `scored_at`

Use SAP-RPT only if the tenant and approved API actually expose it for this use case. Do not claim it is trained from scratch if it uses in-context learning. Use `hana_ml` with PAL/APL only if available and practical. Otherwise use the fallback paths in Section 18.

Predictive results stay in advisory/shadow mode for the prototype and are not included in the regulatory urgency formula.

### SAP AI Core and generative AI hub

Use an approved SAP Cloud SDK for AI or documented API for:

- Model access.
- Embeddings if configured there.
- Prompt templates/orchestration where available.
- Masking and content controls where available.
- Logged, versioned generation.

Do not claim a masking, filtering, grounding, or orchestration feature is active until it is configured and demonstrated in the tenant.

### Investigation Assistant agent (Joule Studio unavailable — see §0)

Joule Studio is not available in the team-11 tenant. Build the same bounded agent as a custom Investigation Assistant: a chat surface in the cockpit whose reasoning loop runs on the AI Core orchestration deployment with tool-calling over the backend API.

> TrustSphere Financial Crime Investigation Agent — custom agent on SAP AI Core orchestration; production surface: Joule Studio.

Recommended tools:

1. `GetAlertDetails`
2. `CalculateRegulatoryUrgency`
3. `RetrieveEntityRelationships`
4. `RetrievePolicyContext`
5. `PredictSLABreach`
6. `AssembleCaseFile`
7. `DraftSupportingNarrative`
8. `StartHumanReview`
9. `SaveDraft`

Prefer four or five reliable tools in the live demo over nine incomplete tools.

The agent may select among approved tools and compose results. It may not file, dismiss, block, change risk weights, edit source data, or approve its own output.

Presentation discipline: this is an agent-pattern demonstration on SAP AI Core. Never call it Joule, and never imply Joule Studio was used. The tool contracts are designed so a Joule Studio skill could call the identical endpoints in production.

### SAP Build Process Automation

Use one deterministic human-review workflow:

1. Receive a case and draft identifier.
2. Assign a human review task.
3. Display evidence coverage and missing-information warnings.
4. Require investigator attestation.
5. Allow approve-for-escalation, return-for-edit, or request-information.
6. Route critical cases to senior review.
7. Save workflow outcome to HANA.
8. Append an audit event.

Do not allow an agent to bypass the human task for a material decision.

### Streamlit

Streamlit is the hackathon investigator cockpit. It is not presented as the bank's permanent global UI.

Required views:

- Ranked alert queue.
- Regulatory urgency and factor breakdown.
- SLA countdown.
- Predictive SLA-risk output marked "advisory."
- Investigation complexity.
- Transaction timeline.
- Relationship path or compact graph.
- Structured case file and data freshness.
- Source citations and missing evidence.
- Supporting narrative editor.
- Human decision controls.
- Audit history.

In a production roadmap, Streamlit may be replaced with SAP Fiori or SAP Build Work Zone without changing backend contracts.

### SAP Analytics Cloud

SAC is optional for the hackathon. If shown, use a realistic mock-up or working aggregate view only after the core flow works.

Allowed management metrics:

- Cost per closed case.
- Median case touch time.
- Aged high-risk backlog.
- Cases approaching SLA breach.
- Payment-hold duration where investigation-related.
- Citation coverage.
- Unsupported-claim rate.
- Team-level rework rate.
- Team-level human override rate.

Do not show individual investigator rankings.

## 6. Shared-state rule

Nothing important may live only in chat state, Streamlit session state, or a local browser.

Persist these in HANA or the selected fallback repository:

- Current case status.
- Assembled `CaseFile`.
- Score and formula version.
- Predictive result and model version.
- Retrieved source references.
- Narrative drafts and versions.
- Workflow task identifiers and status.
- Human decisions and rationale.
- Audit events.

The Joule-to-Streamlit proof is:

1. Joule creates or updates a draft through a backend endpoint.
2. The endpoint persists the draft.
3. Streamlit reloads the case.
4. The same draft is visible with matching version and timestamp.

Both interfaces must call the same service contracts.

## 7. Data model

Use UUIDs or stable synthetic identifiers. Store all timestamps in UTC and render the user's timezone in the UI.

Suggested tables:

### `alerts`

- `alert_id`
- `customer_id`
- `rule_id`
- `alert_type`
- `created_at`
- `sla_due_at`
- `status`
- `source_system`
- `source_updated_at`

### `customers`

- `customer_id`
- `customer_type`
- `risk_rating`
- `jurisdiction_code`
- `pep_flag`
- `restricted_flag`
- `kyc_last_reviewed_at`
- `region`

### `accounts`

- `account_id`
- `customer_id`
- `account_type`
- `opened_at`
- `status`
- `region`

### `counterparties`

- `counterparty_id`
- `name_or_synthetic_label`
- `jurisdiction_code`
- `risk_rating`
- `region`

### `beneficial_owners`

- `owner_id`
- `name_or_synthetic_label`
- `pep_flag`
- `sanctions_reference_flag`
- `region`

### `transactions`

- `transaction_id`
- `account_id`
- `counterparty_id`
- `occurred_at`
- `amount`
- `currency`
- `direction`
- `origin_jurisdiction`
- `destination_jurisdiction`
- `source_system`
- `source_updated_at`

Use fixed-precision decimal types for money. Never use binary floating point for stored financial amounts.

### `alert_factors`

- `alert_id`
- `factor_code`
- `raw_value`
- `normalised_value`
- `weight`
- `weighted_points`
- `reason_code`
- `policy_version`

### `priority_scores`

- `alert_id`
- `urgency_score`
- `urgency_tier`
- `hard_override_code`
- `complexity_band`
- `policy_version`
- `calculated_at`

### `predictive_scores`

- `alert_id`
- `prediction_type`
- `prediction_value`
- `model_name`
- `model_version`
- `feature_snapshot_id`
- `advisory_only`
- `scored_at`

### `cases`

- `case_id`
- `alert_id`
- `assigned_team`
- `status`
- `created_at`
- `updated_at`
- `region`

### `case_files`

- `case_file_id`
- `case_id`
- `schema_version`
- `content_json`
- `assembled_at`
- `source_coverage`

### `source_citations`

- `citation_id`
- `case_file_id`
- `source_type`
- `source_id`
- `source_locator`
- `source_version`
- `retrieved_at`
- `region`
- `permission_scope`

### `narrative_drafts`

- `draft_id`
- `case_id`
- `draft_version`
- `content`
- `generation_id`
- `prompt_version`
- `model_version`
- `created_by_type`
- `created_at`
- `verification_status`

### `decisions`

- `decision_id`
- `case_id`
- `decision_type`
- `rationale`
- `decided_by`
- `attested`
- `decided_at`

### `workflow_instances`

- `workflow_id`
- `case_id`
- `external_instance_id`
- `status`
- `started_at`
- `completed_at`

### `audit_events`

- `event_id`
- `case_id`
- `event_type`
- `actor_type`
- `actor_id`
- `object_type`
- `object_id`
- `details_json`
- `occurred_at`
- `correlation_id`

Application code must not update or delete existing audit events. Append corrections as new events.

## 8. Typed `CaseFile`

Generation receives a typed, permission-checked `CaseFile`, never unrestricted raw database access.

Minimum sections:

```text
CaseFile
├── alert_details
├── priority_explanation
├── predictive_advisories
├── customer_profile
├── counterparty_profiles
├── transaction_timeline
├── entity_relationships
├── related_alerts
├── policy_context
├── historical_case_references
├── missing_information
├── source_provenance
└── data_freshness
```

Every factual item must carry:

- A source identifier.
- A source locator or record key.
- Retrieval timestamp.
- Source version or freshness timestamp where possible.
- Region and permission scope.

If a required value is absent, emit `missing` with a reason. Do not infer it.

## 9. Deterministic regulatory urgency

Keep urgency and effort separate.

### Illustrative urgency weights

These are initial policy assumptions, not discovered truth:

| Factor | Weight |
|---|---:|
| Typology/rule severity | 25% |
| Customer and counterparty risk | 20% |
| Jurisdiction/regulatory exposure | 20% |
| Alert age and SLA proximity | 20% |
| Transaction materiality and velocity | 15% |

Implement:

```text
urgency_score = sum(normalised_factor * configured_weight)
```

Use a stable 0–100 scale. Define tier thresholds in versioned configuration, not scattered constants.

### Hard overrides

Configurable override examples:

- Confirmed sanctions match.
- Terrorist-financing indicator.
- Imminent regulatory SLA breach.
- Previously exited/restricted customer.
- Repeat alert following prior escalation.

A hard override sets the tier and supplies an override reason. It does not erase the underlying factor breakdown.

### Complexity

Calculate separately using operational factors:

- Entity count.
- Jurisdiction count.
- Missing KYC count.
- Source-system count.
- Related-alert count.
- Transaction volume.

Urgency determines queue priority. Complexity supports staffing and sequencing within comparable risk tiers.

### Queue policy

1. Hard overrides first.
2. Regulatory urgency tier.
3. SLA time remaining.
4. Urgency score.
5. Complexity only as an operational tie-break or routing input.
6. Include ageing safeguards and a small quality-control sample so low-ranked alerts do not disappear indefinitely.

Every result must expose factor values, points, policy version, timestamps, and reason codes.

## 10. Predictive SLA-risk layer

Use prediction only for operational support.

Preferred target:

```text
Will this case breach its investigation SLA?
```

Prototype requirements:

- Use synthetic training data or clearly labelled case-study data.
- Split train/test data by case, not by transaction row.
- Avoid target leakage, especially features created after case closure.
- Record the feature schema and model version.
- Return calibrated probability if the selected algorithm supports it.
- Display the result as "Advisory — pilot/shadow mode."
- Do not mix it into regulatory urgency.
- Provide a heuristic fallback with the same response schema.

Minimum evaluation:

- Class balance.
- Precision and recall.
- ROC-AUC or PR-AUC where appropriate.
- Calibration or a reliability check.
- Error breakdown by alert type and jurisdiction.
- Small-sample warning.
- Drift-monitoring design, even if not live.

Do not claim production accuracy from synthetic data.

## 11. HybridRAG

HybridRAG means combining complementary retrieval modes, not merely putting embeddings in HANA.

### Retrieval order

1. Authorise the user and resolve the case region.
2. Retrieve exact structured facts with keyed SQL.
3. Retrieve relevant relationship paths through the graph layer.
4. Retrieve policy and rule context with metadata-filtered vector search.
5. Optionally retrieve permission-filtered historical references.
6. Deduplicate and rank evidence.
7. Assemble the typed `CaseFile`.
8. Generate only from the assembled evidence.
9. Validate citations and numbers before displaying.

### Relationship questions

The graph layer should support at least:

- How is the customer connected to this counterparty?
- Do two entities share a beneficial owner?
- Which related entity appeared in a previous case?
- Which transactions crossed a high-risk jurisdiction?
- What short relationship path supports the alert explanation?

### Vector questions

The vector layer should support:

- Which policy clause governs this alert type?
- What is the monitoring rule's intended risk?
- Which procedure describes missing-information handling?
- Which historical case is semantically similar after permissions and region filtering?

### Citation contract

Generated factual claims must reference citation IDs. Reject or mark unsupported sentences. Numeric values must exactly match cited structured records after normalisation for formatting.

The UI must distinguish:

- Exact fact.
- Relationship inference supported by an explicit path.
- Policy guidance.
- Historical reference.
- AI-generated synthesis.

## 12. Generative outputs

Allowed tasks:

- Explain why an alert is prioritised.
- Summarise a transaction and relationship pattern.
- Identify missing information already represented in the `CaseFile`.
- Draft a supporting investigation narrative.

Required UI label:

> AI-generated draft — investigator verification required. Not approved for filing.

Required controls:

- Sentence- or paragraph-level citations.
- Missing-information warnings.
- Unsupported sections blocked or visibly flagged.
- Numeric consistency checks.
- Prompt, model, source, and output version logging.
- Investigator editing and attestation.
- No filing or submission action.

Prompts must say:

- Use only supplied evidence.
- Do not invent facts.
- Preserve exact numbers and dates.
- Cite every material claim.
- State when evidence is missing or conflicting.
- Do not recommend guilt, SAR filing, payment blocking, or alert dismissal.

## 13. API boundaries

Create a backend service boundary so Streamlit, Joule, and Process Automation share behavior.

Suggested endpoints:

- `GET /health`
- `GET /alerts`
- `GET /alerts/{alert_id}`
- `POST /alerts/{alert_id}/score`
- `POST /alerts/{alert_id}/predict-sla`
- `POST /cases/{case_id}/assemble`
- `GET /cases/{case_id}`
- `POST /cases/{case_id}/explanations`
- `POST /cases/{case_id}/drafts`
- `GET /cases/{case_id}/drafts/latest`
- `POST /cases/{case_id}/review-workflows`
- `POST /cases/{case_id}/decisions`
- `GET /cases/{case_id}/audit-events`

Requirements:

- Validate all payloads with typed schemas.
- Use idempotency keys for create/start operations.
- Use correlation IDs across retrieval, generation, workflow, and audit events.
- Enforce case-region and role checks server-side.
- Use parameterised database operations.
- Return stable error codes and user-safe messages.
- Never log credentials, raw tokens, or unnecessary personal data.

## 14. Governance and security

### Human accountability

Only a human may:

- Dismiss or escalate an alert.
- Approve a supporting narrative.
- Request payment blocking.
- Decide whether a SAR should be filed.
- Approve changes to scoring policy.
- Approve model, prompt, or retrieval changes.

### Formal validation

Do not say the solution avoids validation.

Correct position:

> The design narrows the validation scope by keeping customer-impacting calculations deterministic. Predictive and generative components still undergo formal validation, started in parallel with the pilot.

Validate:

- Unsupported-claim rate.
- Citation coverage and correctness.
- Numerical fidelity.
- Summary accuracy against a human gold set.
- Retrieval relevance and source permission enforcement.
- Performance by jurisdiction, client segment, and alert type.
- Human override behavior.
- Prompt and model regression.
- Security and data leakage.
- Automation bias controls.

### Historical cases

- Access-control and region-filter them.
- De-identify where possible.
- Label as references, not precedent.
- Exclude from urgency scoring.
- Test for biased or incorrect historical outcomes.

### Audit

Record:

- Policy and formula version.
- Predictive model version and features snapshot.
- Prompt and model version.
- Source citations.
- Generated output.
- Human edits.
- Workflow transitions.
- Final decision and attestation.

## 15. Data residency

- Prototype: simulated data in an approved Singapore environment.
- Production: region-local data planes and residency-aware routing.
- Do not move unrestricted customer or case data across regions.
- Apply region metadata to tables, vector documents, graph entities, and citations.
- Enforce region checks in the backend, not only the UI.
- Group reporting must use approved aggregates.

## 16. German Works Council

The audit trail exists for regulated case accountability, quality review, and evidencing—not employee performance management.

Requirements:

- Asia-first pilot.
- Management KPIs aggregated at team level.
- No individual productivity scores, rankings, or leaderboards.
- Named audit access restricted to legitimate case-governance purposes.
- Purpose limitation documented.
- Europe rollout includes a four-to-six-month Works Council workstream in parallel with technical preparation.
- Do not repurpose audit data for HR decisions without the required consultation and approvals.

## 17. Assumptions

Show these visibly in the presentation and retain them in project documentation:

- Prototype data is simulated but representative of production shape.
- Existing monitoring continues to generate alerts; this solution consumes rather than replaces them.
- Alert outputs, KYC data, and approved transaction extracts are accessible through APIs or scheduled files.
- The Singapore BTP/HANA environment satisfies residency requirements for the Asia prototype.
- Production uses region-local data planes.
- Initial urgency weights require Financial Crime policy approval.
- Lighter governance applies only to deterministic prioritisation and workflow, not the whole AI solution.
- Predictive and generative components require formal validation.
- Validation can begin in parallel with a controlled pilot using gold-standard cases.
- SAP-RPT, PAL/APL, HANA graph/SPARQL, vector, Joule Studio, Process Automation, and generative AI hub availability depends on the actual tenant and entitlements.
- English-language cases are in scope for version one.
- Historical cases are permission-filtered references, not authoritative precedent.
- No autonomous closure, escalation, payment block, or SAR filing is permitted.

## 18. Fallback paths

Check gated dependencies during the first hour. Implement adapters so unavailable services do not break the demo.

| Preferred capability | Fallback | Required disclosure |
|---|---|---|
| HANA Cloud | Local relational repository with the same interface | Label as local prototype fallback |
| HANA vector engine | Local vector index behind `VectorRetriever` | Do not claim HANA vector execution |
| HANA graph/SPARQL | Versioned edge tables and bounded SQL graph traversal | Do not claim live SPARQL |
| SAP-RPT | `hana_ml` PAL/APL | Report actual model used |
| PAL/APL | Transparent heuristic or small local baseline behind `SLAPredictor` | Label advisory demonstration |
| Generative AI hub | Approved local/mock deterministic generator | Do not claim AI Core call |
| Joule Studio | **Active:** live Investigation Assistant chat via AI Core orchestration tool-calling against the same backend endpoints | Label as custom agent on SAP AI Core; never present as Joule |
| Process Automation | Local review-state machine matching the workflow contract | Do not claim live SAP workflow |
| SAC | Static management mock-up | Label as target-state concept |

Fallbacks must preserve API contracts and provenance. A reliable, honestly labelled fallback is better than a fabricated integration.

## 19. ROI model

Treat the COO's 30% reduction as a target to validate, not a guaranteed result.

### Case-time arithmetic

Base assumption:

```text
Average escalated case = 2 days × 8 hours = 16 analyst hours
30% target = 4.8 hours saved per case
```

Sensitivity:

| Scenario | Assembly/drafting share | Reduction in that work | Total case-time reduction |
|---|---:|---:|---:|
| Conservative | 40% | 50% | 20% |
| Base | 50% | 50% | 25% |
| Target | 60% | 50% | 30% |

Illustrative annual capacity:

```text
Annual alerts                         12,000
Assumed escalation rate                  20%
Escalated cases                        2,400
Base hours saved per case                4.0
Annual hours recovered                 9,600
Productive hours per FTE               1,800
Equivalent annual capacity               5.3 FTE
Illustrative loaded cost per FTE    US$100,000
Illustrative capacity value        ~US$533,000
```

Target case:

```text
4.8 hours × 2,400 cases = 11,520 hours
11,520 / 1,800 = 6.4 FTE-equivalent capacity
Illustrative value = US$640,000 annually
```

All values other than case-study inputs must be labelled assumptions. Describe results as capacity released, contractor avoidance, attrition absorption, backlog reduction, or faster resolution—not layoffs or guaranteed savings.

Pilot measurements:

- Evidence-gathering time.
- Analysis time.
- Drafting time.
- Reviewer rework.
- Total touch time.
- Cost per closed case.
- High-risk aged backlog.
- SLA breaches.
- Investigation-related payment hold duration.
- Citation coverage.
- Unsupported-claim rate.

## 20. Hack-day priorities

### First hour

Verify, do not assume:

- HANA Cloud connectivity.
- Vector type/search support.
- Knowledge graph and SPARQL availability.
- `hana_ml` and PAL/APL availability.
- SAP-RPT access.
- Generative AI hub model and embedding access.
- Joule Studio access and deployment path.
- Process Automation access.

Record results in `docs/capability-matrix.md`.

### Build order

1. Repository skeleton, schemas, configuration, and interfaces.
2. Data: cleaned provisioned dataset in `TEAM_11_USER` (done — see `docs/data-quality-report.md`); no synthetic seeding needed.
3. HANA/local persistence and migrations.
4. Deterministic urgency engine with tests.
5. Case assembly with provenance.
6. Streamlit end-to-end core flow.
7. One predictive SLA-risk implementation or fallback.
8. Hybrid retrieval adapters and one meaningful relationship path.
9. One cited explanation/narrative generation endpoint.
10. Human decision and audit flow.
11. One Joule agent path.
12. One Process Automation review path.
13. Presentation polish and optional analytics.

### Stop rules

- If the core Streamlit flow is not reliable, stop adding SAP integrations and fix it.
- If a service is unavailable, activate the documented adapter fallback.
- Do not build multiple Joule agents.
- Do not build more than one predictive target.
- Do not expand the ontology beyond what the hero and held-out cases need.
- Do not build SAC before the operational flow works.
- Do not spend demo time on configuration screens.

## 21. Demo flow

Use one alert and one coherent journey:

1. Show the ranked Streamlit queue.
2. Open the hero alert.
3. Explain the deterministic urgency score, reason codes, hard overrides, SLA, and complexity.
4. Show the advisory SLA-breach prediction separately.
5. Assemble the `CaseFile`.
6. Show exact facts, freshness, missing data, and one knowledge-graph relationship path.
7. Ask the Investigation Assistant why the alert is critical (custom agent on AI Core orchestration).
8. Generate a cited supporting narrative.
9. Save the draft to shared HANA state.
10. Switch to Streamlit and show the same draft.
11. Edit, attest, and start the human-review workflow.
12. Show the audit event.

Closing line:

> TrustSphere's existing systems find alerts. Our custom AI on SAP turns those alerts into connected, prioritised, decision-ready investigations without taking accountability away from the investigator.

Do not describe seeded data as "authored backwards." Include:

- One hero case.
- At least two other alert types.
- One irrelevant document/case that retrieval excludes.
- One held-out case or regression fixture.

## 22. Recommended folder structure

```text
.
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── scoring_policy.yaml
│   ├── feature_schema.yaml
│   └── prompts/
├── docs/
│   ├── architecture.md
│   ├── assumptions.md
│   ├── capability-matrix.md
│   ├── data-residency.md
│   ├── governance.md
│   ├── roi-model.md
│   └── demo-script.md
├── data/
│   ├── synthetic/
│   └── fixtures/
├── migrations/
├── src/
│   └── trustsphere/
│       ├── api/
│       ├── config/
│       ├── domain/
│       │   ├── alerts.py
│       │   ├── cases.py
│       │   ├── citations.py
│       │   └── decisions.py
│       ├── persistence/
│       │   ├── base.py
│       │   ├── hana.py
│       │   └── local.py
│       ├── scoring/
│       ├── prediction/
│       │   ├── base.py
│       │   ├── hana_ml.py
│       │   └── heuristic.py
│       ├── retrieval/
│       │   ├── sql.py
│       │   ├── graph.py
│       │   ├── vector.py
│       │   └── hybrid.py
│       ├── generation/
│       ├── workflows/
│       ├── audit/
│       └── services/
├── streamlit_app/
│   ├── app.py
│   ├── pages/
│   └── components/
├── sap/
│   ├── joule/
│   ├── process-automation/
│   └── deployment/
├── scripts/
│   ├── seed_synthetic_data.py
│   ├── check_capabilities.py
│   └── run_demo_checks.py
└── tests/
    ├── unit/
    ├── integration/
    ├── contract/
    ├── evaluation/
    └── fixtures/
```

Keep SAP export artifacts and tenant-specific metadata separate from application logic.

## 23. Coding standards

- Prefer Python 3.11+ unless the SAP runtime requires another supported version.
- Use type hints for public functions and typed validation models at boundaries.
- Keep domain logic independent of Streamlit and vendor SDKs.
- Put SAP-specific code behind interfaces/adapters.
- Use fixed-precision decimals for money.
- Store timestamps in UTC.
- Use parameterised SQL.
- Make create/start operations idempotent.
- Use structured logging with correlation IDs.
- Redact secrets and unnecessary personal data.
- Keep functions small and name business rules explicitly.
- Version scoring policies, feature schemas, prompts, and `CaseFile`.
- Avoid hidden mutable global state.
- Do not silently catch exceptions.
- Convert vendor errors into stable application errors while retaining safe diagnostic context.
- Add concise comments for regulatory or non-obvious design decisions, not for routine syntax.
- Preserve deterministic seed generation with a fixed seed.

## 24. Environment variables

Provide `.env.example` with placeholders only. Never commit real credentials.

Suggested names:

```text
APP_ENV=development
APP_BASE_URL=
LOG_LEVEL=INFO
DEFAULT_REGION=APJ
DISPLAY_TIMEZONE=Asia/Singapore

DATA_BACKEND=hana
HANA_HOST=
HANA_PORT=443
HANA_USER=
HANA_PASSWORD=
HANA_CERT_PATH=
HANA_ENCRYPT=true
HANA_SCHEMA=TRUSTSPHERE

VECTOR_BACKEND=hana
GRAPH_BACKEND=hana
PREDICTION_BACKEND=hana_ml
GENERATION_BACKEND=sap_ai_core
WORKFLOW_BACKEND=sap_build

SAP_AI_CORE_CLIENT_ID=
SAP_AI_CORE_CLIENT_SECRET=
SAP_AI_CORE_TOKEN_URL=
SAP_AI_CORE_BASE_URL=
SAP_AI_CORE_RESOURCE_GROUP=
SAP_AI_MODEL_DEPLOYMENT_ID=
SAP_AI_EMBEDDING_DEPLOYMENT_ID=

SAP_BUILD_API_BASE_URL=
SAP_BUILD_WORKFLOW_DEFINITION_ID=
SAP_BUILD_CLIENT_ID=
SAP_BUILD_CLIENT_SECRET=
SAP_BUILD_TOKEN_URL=

JOULE_AGENT_ID=
JOULE_TENANT_URL=

AUDIT_HASHING_KEY=
CASE_DATA_REGION=APJ
ALLOW_SYNTHETIC_DATA_ONLY=true
ENABLE_HISTORICAL_CASE_RETRIEVAL=false
```

If SAP service bindings provide credentials, prefer the documented binding mechanism and map it inside configuration code. Never print binding payloads.

## 25. Testing and validation

### Unit tests

- Urgency factors and weights.
- Tier boundaries.
- Hard overrides.
- Complexity separation.
- Queue ordering.
- Decimal handling.
- Freshness/missing-data rules.
- Citation formatting.
- Permission and region filters.

### Integration tests

- Persistence round trips.
- Case assembly from seeded records.
- Draft created by one interface and read by another.
- Audit append behavior.
- Workflow idempotency.
- HANA/graph/vector adapters when services are available.

### Contract tests

- Joule tool request/response schemas.
- Process Automation payloads.
- Generative AI gateway responses.
- Stable fallback response shapes.

### Retrieval evaluation

- Exact facts are complete and correct.
- Relevant relationship paths are returned.
- Irrelevant historical material is excluded.
- Region and permission filters cannot be bypassed.
- Policy retrieval prefers effective and authoritative documents.

### Generation evaluation

- Citation coverage.
- Citation correctness.
- Unsupported-claim rate.
- Numerical and date fidelity.
- Missing-information handling.
- No prohibited decision recommendation.
- Regression across prompt/model versions.

### Predictive evaluation

- No leakage.
- Train/test separation by case.
- Baseline comparison.
- Precision/recall and calibration.
- Slice results by alert type and jurisdiction.
- Advisory label always present.

### Demo smoke test

Create one command that verifies:

1. Seed data exists.
2. Queue loads.
3. Hero case scores deterministically.
4. Case assembly returns citations.
5. Prediction returns preferred or fallback result.
6. Explanation/draft returns preferred or fallback result.
7. Draft persists.
8. Decision appends an audit event.

## 26. Truthfulness and capability discipline

This section is mandatory.

- Do not fabricate SAP APIs, SDK methods, entitlements, product features, endpoints, deployment states, or screenshots.
- Do not infer that a feature is available because a marketing page mentions it.
- Verify current tenant availability, runtime version, privileges, and official documentation.
- Record proof in `docs/capability-matrix.md`: capability, tenant/service, test performed, result, fallback, and date.
- Distinguish "implemented and live," "configured but not demonstrated," "mocked," and "target-state."
- Never label local vector retrieval as HANA vector retrieval.
- Never label SQL edge traversal as live SPARQL.
- Never label a local state machine as SAP Build Process Automation.
- Never label a REST chatbot as a Joule agent.
- Never claim SAP-RPT, PAL, or APL was used unless the executed path proves it.
- Never claim the AI output avoids validation.
- Never claim the prototype achieves 30% cost reduction; state the measurable target and assumptions.
- Never claim synthetic-data accuracy generalises to production.
- Never imply the model autonomously learns from investigator decisions.
- Never imply that a human click alone eliminates automation bias.
- Never allow presentation language to exceed what the working code demonstrates.

When uncertain, implement an adapter, activate a clearly labelled fallback, and state the limitation.

## 27. Definition of done

The prototype is done when:

- The Streamlit end-to-end flow works reliably with synthetic data.
- Scores are deterministic, versioned, explainable, and tested.
- Predictive output is separate, advisory, and truthfully labelled.
- The `CaseFile` contains exact facts, relationship evidence, semantic context, missing data, provenance, and freshness.
- Generated text is cited and numerically faithful.
- A narrative draft persists as shared state.
- A human decision is required and audited.
- Region and permission boundaries are represented and tested.
- Joule and Process Automation integrations are either live and demonstrated or replaced by clearly disclosed fallbacks.
- The assumptions and ROI arithmetic are visible.
- The demo completes in under three minutes after the business setup.
- No prohibited autonomous decision exists.
- No SAP capability is fabricated.

Reliability, evidence, and honest integration depth matter more than breadth.
