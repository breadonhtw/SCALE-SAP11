# SAP Reference — verified documentation research (Team 11)

Compiled 2026-07-30 from official SAP sources (help.sap.com, developers.sap.com
tutorial sources on GitHub, SAP's official repos, api.sap.com). Each claim is
marked **[verified]** (read from a primary/official source), **[reported]**
(official-source snippets or reputable secondary sources — spot-check on
tenant), or **[unverified]** (test before relying on it). Per CLAUDE.md §26,
nothing [unverified] may be claimed in the demo without a live tenant probe.

Sections:
1. [HANA Cloud: vector engine, in-DB embeddings, graph](#1-hana-cloud-vector-engine-in-db-embeddings-graph)
2. [SAP AI Core orchestration (generative AI hub)](#2-sap-ai-core-orchestration)
3. [SAP Build Process Automation](#3-sap-build-process-automation)

---

# 1. HANA Cloud: vector engine, in-DB embeddings, graph

**Scraping note:** help.sap.com / developers.sap.com pages are JS-rendered, so
primary content was sourced from the sap-tutorials GitHub markdown that backs
developers.sap.com, SAP's official `langchain-integration-for-sap-hana-cloud`
repo source, and the official HANA Client What's New PDF.

## 1.1 Vector engine SQL reference

Canonical doc: SAP HANA Cloud Vector Engine Guide —
https://help.sap.com/docs/hana-cloud-database/sap-hana-cloud-sap-hana-database-vector-engine-guide/sap-hana-cloud-sap-hana-database-vector-engine-guide

### REAL_VECTOR type
- Introduced HANA Cloud **QRC 1/2024**; elements are REAL (float32); dimensions
  1–65,000. **[reported]**
- Column declaration without dimension is valid (`VECTOR_STR REAL_VECTOR`) —
  **[verified]** in the official RAG tutorial. Fixed form `REAL_VECTOR(<n>)`
  exists **[reported]** — probe `CREATE COLUMN TABLE T (V REAL_VECTOR(768))` on
  tenant; 768-fixed is the sensible declaration for policy embeddings.
- Limitations **[reported]**: no ORDER BY/GROUP BY on the vector column itself;
  REAL_VECTOR cannot be used in arithmetic expressions.

### Functions
- `TO_REAL_VECTOR(<arg>)` — **[verified]**; accepts a JSON-array *string*
  (`'[0.1,0.2,...]'`) bound as a normal parameter, and binary fvecs.
- `COSINE_SIMILARITY(v1,v2)` — range **−1..1**, higher = more similar →
  `ORDER BY ... DESC`. `L2DISTANCE(v1,v2)` → `ORDER BY ... ASC`. **[verified]**
  (SAP's LangChain integration encodes exactly this pairing.)
- `VECTOR_DISTANCE` — **no evidence it exists**; do not code against it.

### Vector index (HNSW) — optional at our scale
Syntax **[verified]** from SAP's LangChain integration source:

```sql
CREATE HNSW VECTOR INDEX <index_name> ON "<schema>"."<table>" ("<vector_column>")
  SIMILARITY FUNCTION COSINE_SIMILARITY   -- or L2DISTANCE
  BUILD CONFIGURATION  '{"M": 64, "efConstruction": 128}'
  SEARCH CONFIGURATION '{"efSearch": 200}'
  ONLINE;
```

- `ONLINE` = shared lock during build; without it, exclusive lock. **[reported]**
- Default (no index) search is an **exact** full scan — fine for a policy corpus
  of 10³–10⁴ chunks. Keep query metric identical to index metric.
- Whether our tenant supports `CREATE VECTOR INDEX` — **[unverified]**, one-line
  DDL probe.

## 1.2 VECTOR_EMBEDDING (in-DB NEB model)

```sql
VECTOR_EMBEDDING(<text>, <'DOCUMENT'|'QUERY'>, 'SAP_NEB.20240715')
SELECT VECTOR_EMBEDDING('Hello world!', 'DOCUMENT', 'SAP_NEB.20240715') FROM DUMMY;
```

- Use `'DOCUMENT'` for stored passages, `'QUERY'` for the search string — the
  asymmetric pair matters for retrieval quality. **[reported]**
- `SAP_NEB.20240715`: output **768 dims**, token limit **256** per input
  **[reported]** → chunk policy text to ≤256 tokens (clause-level chunks, which
  we want for citations anyway). Behavior on oversize input **[unverified]** —
  test.
- Introduced QRC 4/2024; requires the NLP capability on the instance (already
  proven working by `scripts/check_capabilities.py`).
- It's an ordinary scalar function → set-based embedding works
  (`UPDATE ... SET vec = VECTOR_EMBEDDING(chunk_text,'DOCUMENT',...)`).
- **Binding advantage:** embed in-DB on both write and query sides and we only
  ever bind plain strings — no vector serialization anywhere in our code.

## 1.3 Filtered vector search pattern (for RetrievePolicyContext)

```sql
SELECT TOP :k
       DOC_ID, CLAUSE_ID, DOC_TYPE, JURISDICTION, EFFECTIVE_DATE, TEXT,
       COSINE_SIMILARITY(VEC,
         VECTOR_EMBEDDING(:query_text, 'QUERY', 'SAP_NEB.20240715')) AS SIMILARITY
FROM POLICY_CHUNKS
WHERE DOC_TYPE = :doc_type
  AND REGION = :region
  AND EFFECTIVE_DATE <= :as_of
ORDER BY SIMILARITY DESC;
```

- `SELECT TOP k ... ORDER BY <metric>()` is the documented idiom **[verified]**.
- Gotchas: sort direction (DESC for cosine — inverted sort silently returns the
  *worst* matches); metadata predicates compose freely with no index; SAP's
  LangChain `create_where_clause.py` is a good crib for a filter grammar.

## 1.4 HANA Graph (property graph)

Canonical docs: Property Graph Engine reference —
https://help.sap.com/docs/hana-cloud-database/sap-hana-cloud-sap-hana-database-property-graph-engine-reference/opencypher-table-sql-function

### CREATE GRAPH WORKSPACE — **[verified]**

```sql
CREATE GRAPH WORKSPACE "TS_GRAPH"
    EDGE TABLE "EDGES"
        SOURCE COLUMN "SOURCE"
        TARGET COLUMN "TARGET"
        KEY COLUMN "EDGE_ID"
    VERTEX TABLE "ENTITIES"
        KEY COLUMN "ENTITY_ID";
```

Both tables need a key; edge SOURCE/TARGET must not be NULL; FKs to the vertex
key recommended ("prevent dangling edges"). Simplest fit for us: one `ENTITIES`
vertex table (companies + owners + counterparties + alerts, with a type column)
and one `EDGES` table (`EDGE_TYPE` = OWNS/CONTROLS/SENT_TO/...).

### Querying — practical options from hdbcli

1. **`OPENCYPHER_TABLE`** — ordinary table function, plain `cursor.execute`:

   ```sql
   SELECT * FROM OPENCYPHER_TABLE( GRAPH WORKSPACE "SCHEMA"."TS_GRAPH" QUERY
     'MATCH (a)-[e]->(b)
      WHERE a.entity_type = ''COMPANY'' AND b.pep_flag = ''TRUE''
      RETURN a.entity_id AS src, e.edge_id AS edge, e.edge_type AS rel, b.entity_id AS dst'
   );
   ```

   Inner quotes doubled; **no hdbcli `?` binding inside the openCypher string**
   — sanitize/whitelist interpolated IDs. Variable-length paths (`-[*1..3]->`)
   **[unverified]** on HANA's openCypher subset — probe.
2. **GraphScript procedures (`LANGUAGE GRAPH`)** — needed for shortest path /
   k-hop; callable via `CALL`. `Shortest_Path(:g, :v_start, :v_end, :direction)`
   returns a `WeightedPath` whose `EDGES(:p)` projects to an ordered edge list —
   exactly the "relationship path with source identifiers" the CaseFile needs.
   `Neighbors(:g, :v, :minDepth, :maxDepth)` for k-hop. Full worked examples:
   https://developers.sap.com/tutorials/hana-cloud-smart-multi-model-8.html
3. **HANA-Cloud caveat [verified]:** calculation-scenario graph nodes and
   `MATCH_SUBGRAPHS` are HANA 2.0 on-prem only. No standalone SQL `MATCH` —
   openCypher goes through `OPENCYPHER_TABLE`.

**Recommendation:** one GraphScript `Shortest_Path` procedure for the path
display + `OPENCYPHER_TABLE` for "shared beneficial owner" pattern queries.

## 1.5 hdbcli specifics

- hdbcli 2.29 fully supports REAL_VECTOR (support landed ~2.21). **[verified]**
- Binding vectors (only needed if not embedding in-DB):
  1. String through `TO_REAL_VECTOR(?)` binding `str(list_of_floats)` —
     simplest, works with `executemany`. **[verified]**
  2. Binary fvecs: `struct.pack(f"<I{len(v)}f", len(v), *v)`. **[verified]**
     (SAP's LangChain integration does this.)
- Fetching: REAL_VECTOR comes back as fvecs bytes —
  `dim = struct.unpack_from('<I', buf)[0]; vals = struct.unpack_from(f'<{dim}f', buf, 4)`.
  In practice we fetch similarity scores + text, not raw vectors.

## 1.6 Pitfalls / version notes

- Use the **hana-cloud-database** doc set only; on-prem HANA 2.0 pages mislead
  (both directions: vector features are Cloud-only, some graph features are
  on-prem-only).
- COSINE_SIMILARITY range is −1..1 (blogs saying 0..1 assume non-negative
  embeddings) — thresholds must assume −1..1.
- SAP_NEB token limit 256 → clause-level chunking.
- Suggested one-statement tenant probes to close [unverified] items:
  `REAL_VECTOR(768)` fixed-dim column; `CREATE HNSW VECTOR INDEX`; openCypher
  `-[*1..2]->`; hdbcli list-binding to a vector param; oversize
  `VECTOR_EMBEDDING` input.

### Key sources
- RAG + HANA vector tutorial (verified SQL/hdbcli patterns):
  https://developers.sap.com/tutorials/ai-core-genai-hana-vector..html
- SAP LangChain integration (index DDL, fvecs packing, filter grammar):
  https://github.com/SAP/langchain-integration-for-sap-hana-cloud
- Graph workspace: https://developers.sap.com/tutorials/hana-cloud-smart-multi-model-7.html
  · shortest path: https://developers.sap.com/tutorials/hana-cloud-smart-multi-model-8.html
- HANA Client What's New (driver matrix):
  https://help.sap.com/doc/d2e913b3fc5d4617bce67da49b2ae348/2.24/en-US/SAP_HANA_Client_Whats_New_Guide_en.pdf
- NLP/VECTOR_EMBEDDING enablement:
  https://community.sap.com/t5/technology-blog-posts-by-sap/new-machine-learning-and-nlp-features-in-sap-hana-cloud-2024-q4/ba-p/13953934

---

# 2. SAP AI Core orchestration

**Evidence basis:** official Orchestration OpenAPI specs (v1 + v2) vendored in
SAP's `ai-sdk-js` repo, the `sap-ai-sdk-gen` 7.2.0 wheel source inspected
locally, help.sap.com SDK references, SAP tutorials. Verified 2026-07-30.

**Headline facts:**
- Our one RUNNING `orchestration` deployment is all we need — harmonized
  per-request access to every tenant-enabled model. No per-model deployments.
- The same deployment serves **two incompatible API versions**: v1
  `POST {deploymentUrl}/completion` and v2 `POST {deploymentUrl}/v2/completion`.
  **Use v2** + `gen_ai_hub.orchestration_v2` and stay consistent.
- **OpenAI-style tool calling IS supported** (v1 and v2) — `tools` on the
  template, `tool_calls` in responses, `role:"tool"` messages via
  `messages_history`. **`tool_choice` is NOT supported** — don't design a flow
  that forces a specific tool; steer via system prompt.
- Maintained Python package: **`sap-ai-sdk-gen` 7.2.0** (import namespace still
  `gen_ai_hub`). `generative-ai-hub-sdk` is deprecated/inactive — distrust old
  blogs/courses naming it.

## 2.1 Deployment URL discovery

```http
GET {AI_API_URL}/v2/lm/deployments?scenarioId=orchestration&executableIds=orchestration&status=RUNNING
Authorization: Bearer <token>
AI-Resource-Group: team-11
```

→ take `deploymentUrl` (form `https://api.ai.{region}.ml.hana.ondemand.com/v2/inference/deployments/{id}`).
The SDK auto-discovers (newest RUNNING); pin `deployment_id` for determinism.
OAuth: client-credentials against `{service-key url}/oauth/token`. Send
`AI-Resource-Group` on every call.

## 2.2 v2 completion request (the shape we build against)

```json
POST {deploymentUrl}/v2/completion
{
  "config": {
    "modules": {
      "prompt_templating": {
        "prompt": {
          "template": [
            {"role": "system", "content": "Use only supplied evidence."},
            {"role": "user", "content": "Explain alert {{?alert_id}} using: {{?case_file_json}}"}
          ],
          "defaults": {}
        },
        "model": {
          "name": "<verified-model-name>",
          "version": "latest",
          "params": {"max_completion_tokens": 800, "temperature": 0.1}
        }
      }
    }
  },
  "placeholder_values": {"alert_id": "ALERT-001", "case_file_json": "{...}"}
}
```

- Placeholder syntax `{{?name}}`; unset placeholders without defaults → request
  fails.
- `modules` requires only `prompt_templating`; `filtering`, `masking`,
  `grounding`, `translation` are optional add-ons — **no orchestration-level
  content filter runs unless configured** (per CLAUDE.md §26, never claim
  filtering/masking is active until configured and demonstrated).
- Templates support `response_format`: `text` | `json_object` | `json_schema`
  (with `strict`) — **`json_schema` is a good fit for the citation-contract
  output validation in B2.**
- v1→v2 renames (for reading old examples): `orchestration_config`→`config`,
  `module_configurations`→`modules`, `input_params`→`placeholder_values`,
  `templating_module_config`+`llm_module_config`→`prompt_templating`
  (`prompt`+`model`), response `module_results`/`orchestration_result`→
  `intermediate_results`/`final_result`.

## 2.3 Model selection and discovery

- Model choice is **per-request** via `model.name`; server validates against
  tenant entitlements. A wrong name returns a 400 whose message **lists the
  allowed set** — a crude but effective discovery mechanism.
- Discovery endpoint: `GET {AI_API_URL}/v2/lm/scenarios/foundation-models/models`
  (+ AI Launchpad Model Library). Canonical availability list: SAP Note 3437766.
- Naming format `provider--model` for non-Azure providers (e.g.
  `anthropic--claude-4.5-sonnet`); OpenAI names bare (`gpt-4o`, `gpt-4o-mini`).
  Only `gpt-4o-mini` appears in the official spec — **all other names must be
  verified with a live call before appearing in the capability matrix.**
- Use `max_completion_tokens` not `max_tokens` for OpenAI models (deprecated in
  Azure OpenAI spec; newer models may reject it).

## 2.4 Python SDK (`sap-ai-sdk-gen`)

- `pip install "sap-ai-sdk-gen[all]"` — 7.2.0, Python ≥3.9, langchain included
  in `all`. Imports stay `gen_ai_hub.*`.
- Auth via env vars: `AICORE_AUTH_URL`, `AICORE_CLIENT_ID`,
  `AICORE_CLIENT_SECRET`, `AICORE_BASE_URL`, `AICORE_RESOURCE_GROUP` (or
  `~/.aicore/config.json` profiles). The client appends `/oauth/token` to
  `AICORE_AUTH_URL`.

```python
from gen_ai_hub.orchestration_v2 import (
    OrchestrationService, Template, SystemMessage, UserMessage,
    LLMModelDetails, OrchestrationConfig, ModuleConfig, PromptTemplatingModuleConfig)

template = Template(template=[SystemMessage(content="You are a helpful assistant."),
                              UserMessage(content="{{?user_query}}")])
llm = LLMModelDetails(name="gpt-4o", params={"max_completion_tokens": 512})
config = OrchestrationConfig(modules=ModuleConfig(
    prompt_templating=PromptTemplatingModuleConfig(prompt=template, model=llm)))
service = OrchestrationService(config=config)
result = service.run(placeholder_values={"user_query": "Hello"})
print(result.final_result.choices[0].message.content)
```

## 2.5 Tool calling (the B4 Investigation Assistant mechanism)

Verified in both OpenAPI specs and SDK source:

- **Request:** `tools` array on the `Template` — `{type:"function", function:
  {name (≤64, ^[a-zA-Z0-9-_]+$), description, parameters (JSON Schema),
  strict}}`.
- **Response:** `choices[].message.tool_calls` = `{id, type:"function",
  function:{name, arguments}}`. Spec warns arguments "may not always be valid
  JSON" — validate before executing.
- **Loop:** execute the tool client-side, then send the assistant tool-call
  message + a `{role:"tool", tool_call_id, content}` message back via
  `messages_history` (merged with template messages) on the next request.
- **SDK support:** `FunctionTool.from_function(fn, strict=True)`,
  `@function_tool()` decorator (schema from signature/docstring),
  `tool_call.function.parse_arguments()`, `ToolChatMessage(...)`. All in
  `gen_ai_hub.orchestration_v2`.
- **Gap:** `tool_choice` absent from spec and SDK. Undocumented pass-through
  via params is unverified — do not depend on forced tool choice.

**Design conclusion:** the CLAUDE.md §0 assistant plan is fully supported —
define the 4–5 backend tools as `FunctionTool`s over FastAPI endpoints, run the
loop client-side. No direct-model-deployment path needed.

## 2.6 Streaming, usage, errors

- **Streaming:** SSE. v2: `config.stream: {enabled: true, chunk_size, delimiters}`;
  chunks carry `delta` not `message`. SDK: `service.stream(...)`; async
  `arun`/`astream` exist.
- **Usage:** `final_result.usage` = `{completion_tokens, prompt_tokens,
  total_tokens}`; in streaming set `stream_options: {include_usage: true}`.
- **Response envelope (v2):** `{request_id, final_result (choices, usage,
  citations), intermediate_results (per-module), intermediate_failures[]}`.
  Note: `citations` is for Perplexity-style web citations — NOT our CaseFile
  citation contract.
- **Errors:** `{error: {request_id, code, message, location (e.g. "LLM
  Module"), ...}}`; `finish_reason: "content_filter"` = provider-side filter.
  The v2 SDK auto-retries **only HTTP 429** (honors Retry-After). Rate limits:
  no published numbers — mark "undocumented" in the capability matrix.

## 2.7 Pitfalls

1. v1/v2 field-rename confusion is the #1 trap — pick v2 everywhere.
2. Deployment also serves `/v2/embeddings` — irrelevant for us (HANA in-DB
   embeddings), but don't confuse the endpoints.
3. Placeholders without defaults are required — missing ones fail the request.
4. Model names are tenant-dependent — verify live before claiming (B2 task).
5. Old package name `generative-ai-hub-sdk` in docs/blogs — install
   `sap-ai-sdk-gen`, import `gen_ai_hub`.

### Key sources
- v2 OpenAPI spec: https://github.com/SAP/ai-sdk-js/blob/main/packages/orchestration/src/spec/api.yaml
  · v1: https://github.com/SAP/ai-sdk-js/blob/v1.18.0/packages/orchestration/src/spec/api.yaml
- Orchestration docs: https://help.sap.com/docs/sap-ai-core/sap-ai-core-service-guide/orchestration
- Python SDK v2 reference (tools/streaming): https://help.sap.com/doc/generative-ai-hub-sdk/CLOUD/en-US/_reference/orchestration-service2.html
  · index: https://help.sap.com/doc/generative-ai-hub-sdk/CLOUD/en-US/index.html
- PyPI: https://pypi.org/project/sap-ai-sdk-gen/
- Tutorials: https://developers.sap.com/tutorials/ai-core-orchestration-consumption-v2..html
- Supported models: https://help.sap.com/docs/sap-ai-core/sap-ai-core-service-guide/supported-models + SAP Note 3437766
- Local copies: both spec YAMLs + SDK wheel source saved under the session
  scratchpad (`sdkx/gen_ai_hub/`).

---

# 3. SAP Build Process Automation

**Scraping note:** api.sap.com spec downloads are login-gated; evidence comes
from official SAP tutorial sources (GitHub `sap-tutorials/sap-build-process-automation`,
which backs developers.sap.com), SAP KBAs, and SAP community blogs. Endpoint
paths must be confirmed against our own tenant's trigger **View** dialog before
coding.

## 3.1 Triggering a process via API

- A process starts from a trigger: form, **API call**, or event. Add in Process
  Builder: process → **Add a Trigger → Call an API**. Process inputs configured
  under Process Details → Variables → Configure → Add Input. **Field names /
  casing / whitespace must exactly match what the caller sends.** [verified —
  tutorial spa-create-process-api-trigger]
- Trigger changes take effect **only after release + deploy**; already-deployed
  processes keep old triggers at runtime. The "Public" environment is
  deprecated — create our own/shared environment.
- Start-instance call (SBPA Workflow API, `SPA_Workflow_Runtime` on api.sap.com):

  ```http
  POST {api-gateway-base}/public/workflow/rest/v1/workflow-instances
  Authorization: Bearer <oauth token>
  api-key: <environment API key>          # required with client credentials
  Content-Type: application/json

  { "definitionId": "<process definition ID>",
    "context": { "case_id": "...", "draft_id": "...", "evidence_summary": "..." } }
  ```

  Some SAP sources show the path **without** `/public` — **do not hardcode;
  copy the exact URL, definitionId, and payload skeleton from Control Tower →
  Environments → <env> → Triggers → View** (authoritative per-tenant).
- No-code validation path before writing Python: environment → Processes and
  Workflows → Start New Instance with a JSON payload. Instance monitoring:
  SAP Build home → Monitoring → Process and Workflow Instances (status, logs,
  full Context).

## 3.2 Authentication

- BTP cockpit → Instances and Subscriptions → Create → SAP Build Process
  Automation, plan **standard (Instance)** → **Create Service Key**. Key fields:
  **`api`** (region API-gateway base — the only trustworthy source for our ap11
  host), `clientid`, `clientsecret`, `url` (XSUAA base). On free tier an
  instance `sap_process_automation` + key is auto-created — reuse it.
- OAuth client-credentials: POST `{url}/oauth/token`, `grant_type=client_credentials`.
- **Two auth models** (2025+ mechanism — newer than most blogs):
  - User token (auth-code flow): permissions via the user's role collections;
    no API key.
  - **Client credentials (technical user): must ALSO send an environment-scoped
    API key in header `api-key`** (created in Control Tower, carries scopes;
    publicly named scopes: `trigger_read`, `environment_read`; exact
    start-instance scope name **[unverified]** — confirm in tenant).
- Task-completion roles: caller must be `ProcessAutomationAdmin` or the task
  recipient with `ProcessAutomationParticipant`. With pure client credentials a
  task shows as completed by a generic technical user — **for our audit trail
  the human must complete the task in the inbox UI**, not the backend.

## 3.3 Reading status and outcomes (verify paths in tenant Try Out first)

| Purpose | Endpoint |
|---|---|
| List instances | `GET .../workflow/rest/v1/workflow-instances?environmentId=<env>` (also `definitionId`, `status`) |
| Instance status | `GET .../workflow-instances/{id}` — RUNNING / SUSPENDED / COMPLETED |
| Instance context (incl. outcome vars) | `GET .../workflow-instances/{id}/context` |
| List user tasks | `GET .../task-instances` (filter by instance / status READY) |
| Task context | `GET .../task-instances/{id}/context` |
| Complete task programmatically | `PATCH .../task-instances/{id}` body `{"status":"COMPLETED","decision":"<button id>","context":{...}}` — 403 no privilege, 404 no task, 400 already done, 409 unknown decision |

**Outcome back to FastAPI — two patterns:**
1. **Polling (demo default):** poll instance until COMPLETED, then read
   `/context` for the decision (bind the approval outcome to a process
   variable). Entirely within verified API surface; works with localhost.
2. **Callback (push):** final **action step** in the process POSTs to our
   backend — requires an Action project (upload FastAPI's `openapi.json`,
   pick operation, Test → Release → Publish) + a BTP destination with property
   `sap.processautomation.enabled=true`, imported in Control Tower →
   Destinations, assigned via an Environment Variable of type Destination.
   Destination proxy = Internet → **localhost cannot receive the callback**;
   needs a public HTTPS URL. No generic completion-webhook feature exists.

## 3.4 Click-work map (design time is NOT API-able)

Runtime = REST. Design time (process model, forms, actions, triggers, release,
deploy) = lobby UI only; no supported public API found for programmatic
process deployment.

1. Lobby → Create → Build an Automated Process → Business Process project.
2. API trigger + inputs (`case_id`, `draft_id`, `evidence_summary`).
3. Approval step: bind process inputs into read-only form fields via the step's
   **Inputs** section. **API-triggered processes have no initiating user** —
   "Process Started By" resolves to nothing; set explicit recipients.
4. **Approval forms have exactly 2 buttons** (labels customizable via gear →
   Button title, e.g. "Approve for escalation" / "Return"). For our 3-outcome
   design (approve / return-for-edit / request-information), pragmatic options:
   (a) required dropdown "Requested action" + Condition step branching on it,
   or (b) two-level form (Reject branch → second form distinguishing
   return vs request-info). Custom SAPUI5 tasks allow arbitrary buttons but are
   too heavy for the hackathon.
5. Release → Deploy to environment; copy URL/definitionId from trigger View.

## 3.5 Task UI

- **My Inbox in the SAP Build lobby** — standard surface, no extra setup.
  (Work Zone / SAP Task Center possible; Task Center adds ~1 min lag.)
- Assignee must exist as a tenant user, be a recipient on the step, and hold
  `ProcessAutomationParticipant`.

## 3.6 Pitfalls

1. **Legacy vs current:** SAP Workflow Management (host `api.workflow-sap...`,
   SAP Cloud SDK `WorkflowInstancesApi`) was retired 2023 — code only against
   the SBPA API-gateway host from our service key + the SAPProcessAutomation
   package on api.sap.com. Distrust 2022–2023 blogs (predate environments/API
   keys).
2. **422 on start** = context payload doesn't match configured input names /
   casing — the most common trigger failure.
3. **403 with client credentials** = missing `api-key` header or missing scope
   on the key; 403 on task PATCH = caller not admin/recipient.
4. Destination "Check Connection" showing **401 is expected/fine** for
   OAuth2ClientCredentials destinations.
5. Budget time for release→deploy cycles (trigger changes need redeploy).
6. Free-tier execution quotas exist; standard entitlement unlikely to bite.

## 3.7 Build order for our one workflow (B5)

1. BTP cockpit: SBPA service instance (standard) + service key → note `api`,
   `clientid`, `clientsecret`, `url`.
2. Lobby: process with API trigger; inputs `case_id`/`draft_id`/
   `evidence_summary`; approval form (custom labels, explicit demo-investigator
   recipient); condition branch for the 3-outcome pattern; release + deploy;
   copy URL/definitionId from trigger View.
3. Control Tower: environment API key with trigger/read scopes.
4. FastAPI: token → POST workflow-instances (with `api-key`); persist
   `workflow_instance_id` to `WORKFLOW_INSTANCES`; **poll** status/context;
   persist outcome + audit event.
5. Human completes the task in My Inbox. Any failure → documented fallback:
   local review-state machine, honestly labelled.

### Key sources
- API trigger tutorial: https://developers.sap.com/tutorials/spa-create-process-api-trigger.html
  · test: https://developers.sap.com/tutorials/spa-run-process-api-trigger.html
- Service instance/key + destination: https://developers.sap.com/tutorials/spa-create-service-instance-destination.html
- Actions (OpenAPI upload): https://developers.sap.com/tutorials/spa-process-action-create.html
  · destination flag: https://developers.sap.com/tutorials/spa-create-destination.html
- Workflow API: https://api.sap.com/api/SPA_Workflow_Runtime/overview
  · package: https://api.sap.com/package/SAPProcessAutomation/all
- API endpoint KBA: https://userapps.support.sap.com/sap/support/knowledge/en/3501325
- API keys/scopes: https://community.sap.com/t5/technology-blog-posts-by-sap/guide-to-process-automation-apis-part-ii-api-keys/ba-p/14292296
- 422 explanation: https://blogs.sap.com/2023/07/23/api-trigger-failed-heres-why-you-got-422-error/
