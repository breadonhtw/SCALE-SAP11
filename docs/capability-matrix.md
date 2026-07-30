# Capability Matrix — team-11 tenant

Date tested: 2026-07-30
Test method: `scripts/check_capabilities.py` (live probes against the tenant, no assumptions)
Tenant: HANA Cloud 4.00.000.00.1781736874 (Singapore region), AI Core resource group `team-11`

| Capability | Tenant/service | Test performed | Result | Decision / fallback |
|---|---|---|---|---|
| HANA Cloud SQL | HANA Cloud, user TEAM_11_USER | Connect + query via hdbcli 2.29.25 | ✅ **PASS** | Primary system of record. Read-only `TRUSTSPHERE_REFERENCE`, writable `TEAM_11_USER`. |
| HANA vector engine | same | `TO_REAL_VECTOR`, `COSINE_SIMILARITY` executed | ✅ **PASS** | Use native HANA vector search for policy retrieval. |
| In-DB embeddings | same | `VECTOR_EMBEDDING(…, 'SAP_NEB.20240715')` executed | ✅ **PASS** | Embed policy/rule text **inside HANA** — no external embedding pipeline needed. |
| HANA graph (property graph) | same | `CREATE GRAPH WORKSPACE` created + dropped successfully in TEAM_11_USER | ✅ **PASS** | Build the entity-relationship layer as a HANA Graph workspace over company/owner/transaction edge tables. |
| SPARQL / triple store | same | `SPARQL_EXECUTE` call | ❌ **FAIL** — "No active TripleStore found in landscape" | Use HANA Graph workspace (property graph) instead. **Label as HANA Graph, never as SPARQL/RDF.** |
| PAL / APL | same | `_SYS_AFL` PAL procedure catalog + granted AFL roles | ❌ **FAIL** — 0 procedures visible, no AFL role | SLA-risk model trained locally (open-source, e.g. scikit-learn) behind the `SLAPredictor` interface; scores persisted to HANA. **Never claim PAL/APL execution.** |
| `hana_ml` package | client side | import + version | ✅ installed (2.29.26072700) | Usable as HANA dataframe client; its PAL algorithms are unusable on this tenant (see above). |
| AI Core generative hub | AI Core, resource group team-11 | OAuth + `GET /v2/lm/deployments` | ✅ **PASS** — 1 deployment RUNNING: **orchestration** (id `d50865cd41ba097c`) | Generation goes through the orchestration deployment (harmonized API: templating, model access). Which LLMs it exposes → verify during generation phase before claiming a specific model. |
| Joule Studio | SAP Build lobby | User re-checked tenant (2026-07-30) | ❌ **NOT AVAILABLE** (initial confirmation was mistaken) | **Fallback active:** in-cockpit Investigation Assistant chat calling the same backend tools via AI Core orchestration — labelled as a custom agent demonstrating the pattern, **never labelled as Joule**. Optional: Joule Studio target-state mock, clearly labelled. |
| SAP Build Process Automation | SAP Build lobby | User confirmed entitlement in tenant UI (2026-07-30) | ✅ **ACCESS CONFIRMED** — workflow not yet built | Build one human-review workflow (Phase 5). Fallback stays available: local review-state machine matching the workflow contract, clearly labelled. |
| SBPA API trigger (service key) | BTP cockpit, shared subaccount | Attempted create service instance + create service key on existing instances (2026-07-30) | ❌ **BLOCKED** — user lacks service-management rights in the shared subaccount (both actions refused); no service key ⇒ no client-credentials auth for the Workflow API | **Hybrid stance:** process built + demonstrated live in the SAP Build lobby (manual start + My Inbox approval); backend API-trigger code (`workflows/sbpa.py`) implemented + unit-tested, dormant until a service key exists; cockpit keeps the honest local-fallback label. Unblock path: role grant from subaccount owner. |
| SAP-RPT | — | Not exposed anywhere in tenant credentials/APIs found | ❌ assumed unavailable | Not used; do not mention as implemented. |
| Orchestration completion (v2) | AI Core, resource group team-11 | Live `POST {deploymentUrl}/v2/completion` via `scripts/verify_orchestration.py` | ✅ **PASS** (2026-07-30) — deployment `d50865cd41ba097c` at `api.ai.prod-ap11.ap-southeast-1.aws...`. Verified models: **`gpt-4.1-mini-2025-04-14`** (team's choice via AI Launchpad Generative AI → Orchestration; `request_id 0c352602-92ab-9d1a-8893-10f5a9e35d52`) and `gpt-4o-mini-2024-07-18` (`request_id 5601bf01-...`) | **Generation model: `gpt-4.1-mini`** (default in code, override via `TRUSTSPHERE_GEN_MODEL`). Any new model must be re-verified with the same script before being claimed. |
| Orchestration content filtering | same deployment | Live v2 completion with `filtering` module (Azure Content Safety + Llama Guard 3 8B, config mirrored from portal export `sap/orchestration/trustsphere-narrative-config.json`) | ✅ **PASS** (2026-07-30) — `intermediate_results` shows `input_filtering` + `output_filtering` executed; fincrime case content not blocked. ⚠️ Llama Guard service returned one transient 503 ("response could not be parsed") — client now retries 503 once. | Filtering **active** in the generation pipeline (input+output). Portal cannot save orchestration configs in this tenant's Launchpad release — the exported JSON in `sap/orchestration/` is the governed artifact; code mirrors it. |

## Architecture consequences

1. **HybridRAG is fully native**: exact facts (SQL) + relationships (HANA Graph workspace) + semantic retrieval (HANA vector engine with in-DB NEB embeddings). No local vector-index fallback needed.
2. **Predictive layer is the one honest fallback**: local open-source model (permitted by case rules — must be open-source/free tier), advisory-only, scores written back to HANA with model version. Presented as "demonstration of the pattern; production target is PAL/APL or approved model service."
3. **Generation** uses the AI Core orchestration deployment; prompt templates and model config verified at build time, logged per CLAUDE.md §12.
4. **Joule + Process Automation** need a manual tenant-UI check (browser) before the demo plan is final — until then both are planned with disclosed fallbacks.

## Open items

- [x] Identify which LLM models the orchestration deployment serves — provider list confirmed via tenant UI; concrete model verified at generation build time.
- [x] Manual check: Joule Studio access — **NOT available** (re-checked 2026-07-30); fallback path active.
- [x] Manual check: Process Automation entitlement — **confirmed 2026-07-30**.
- [x] LLM availability — model library in the tenant lists providers: Amazon, Anthropic, Cohere, Google, Mistral AI, NVIDIA, OpenAI, Perplexity, SAP (user screenshot, 2026-07-30). Exact model chosen + verified with a live orchestration call at generation build time before any claim.
