"""DEPRECATED — Track A's real backend replaced this on 2026-07-30.

Use `uvicorn trustsphere.api.app:app --port 8000` (local SQLite fallback or
HANA per DATA_BACKEND). This mock predates docs/api-contract.md and no longer
matches it; kept only as a reference for the pre-merge provisional shapes.

Original description: Mock backend for Track B development (B1 scope).

PROVISIONAL SHAPES — docs/api-contract.md is Person A's deliverable and does
not exist yet. Until it is published and jointly signed off, the shapes served
here (and consumed by streamlit_app/components/api_client.py) are the working
proposal. Reconcile both files against the contract when it lands.

Endpoints (B1 only; B2/B3 will extend):
  GET  /health
  GET  /alerts?status=open|all|<status>&tier=&limit=&offset=
  GET  /alerts/{alert_id}
  GET  /alerts/{alert_id}/relationships
  POST /alerts/{alert_id}/case            (get-or-create)
  POST /cases/{case_id}/assemble
  GET  /cases/{case_id}

Field vocabulary mirrors the cleaned TEAM_11_USER snapshot (ALERT_PRIORITY,
STATUS in OPEN/INVESTIGATING/CLOSED_TRUE/CLOSED_FALSE, LEGAL_NAME,
REGISTRATION_NUMBER/LEI_CODE, KYC_EFFECTIVE_STATUS, AMOUNT_USD as decimal
string, INITIATED_AT, OWNER/OWNERSHIP_PERCENTAGE) so swapping in the real
backend does not change UI code. Money is always a decimal string.

Run:  uvicorn mock_api.app:app --port 8000 --reload
"""

from __future__ import annotations

import copy
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from trustsphere.generation import get_generator  # noqa: E402
from trustsphere.generation.validation import validate  # noqa: E402

FIXTURES = _ROOT / "data" / "fixtures"

app = FastAPI(title="TrustSphere mock backend", version="0.1.0")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


_ALERTS_DOC = _load("alerts.json")
ALERTS: dict[str, dict] = {a["alert_id"]: a for a in _ALERTS_DOC["alerts"]}
ALERTS_META = {k: v for k, v in _ALERTS_DOC.items() if k != "alerts"}

# In-memory app state (real backend persists all of this to HANA).
CASES: dict[str, dict] = {}
CASE_BY_ALERT: dict[str, str] = {}
CASE_FILES: dict[str, dict] = {}
DRAFTS: dict[str, list[dict]] = {}  # case_id -> versions (append-only)
AUDIT: dict[str, list[dict]] = {}  # append-only per case


def _err(status: int, code: str, message: str, correlation_id: str) -> HTTPException:
    return HTTPException(status_code=status, detail={
        "error": {"code": code, "message": message, "correlation_id": correlation_id}})


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {
        "error": {"code": "INTERNAL", "message": str(exc.detail), "correlation_id": ""}}
    return JSONResponse(status_code=exc.status_code, content=detail)


def _corr(x_correlation_id: str | None) -> str:
    return x_correlation_id or f"corr-{uuid.uuid4().hex[:12]}"


def _audit(case_id: str, event_type: str, actor_type: str, actor_id: str,
           object_type: str, object_id: str, details: dict, correlation_id: str) -> dict:
    event = {
        "event_id": f"EVT-{uuid.uuid4().hex[:10]}",
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "object_type": object_type,
        "object_id": object_id,
        "details": details,
        "occurred_at": _now(),
        "correlation_id": correlation_id,
    }
    AUDIT.setdefault(case_id, []).append(event)
    return event


def _queue_item(a: dict) -> dict:
    keep = ["alert_id", "alert_type", "alert_priority", "status", "created_at",
            "company", "urgency", "complexity", "sla", "advisory", "queue_rank",
            "region"]
    item = {k: a.get(k) for k in keep}
    item["case_id"] = CASE_BY_ALERT.get(a["alert_id"])
    return item


def _get_alert(alert_id: str, correlation_id: str) -> dict:
    if alert_id not in ALERTS:
        raise _err(404, "ALERT_NOT_FOUND", f"No alert {alert_id}", correlation_id)
    return ALERTS[alert_id]


def _get_case(case_id: str, correlation_id: str) -> dict:
    if case_id not in CASES:
        raise _err(404, "CASE_NOT_FOUND", f"No case {case_id}", correlation_id)
    return CASES[case_id]


@app.get("/health")
def health():
    return {"status": "ok", "backend": "mock", "version": "0.1.0"}


@app.get("/alerts")
def list_alerts(status: str = "open", tier: str | None = None,
                limit: int = 50, offset: int = 0):
    # Queue order is server-side policy (overrides -> tier -> SLA -> score);
    # fixtures carry it as queue_rank and clients must not re-sort.
    items = sorted(ALERTS.values(), key=lambda a: a["queue_rank"])
    if status == "open":
        items = [a for a in items if a["status"] in ("OPEN", "INVESTIGATING")]
    elif status != "all":
        items = [a for a in items if a["status"].lower() == status.lower()]
    if tier:
        items = [a for a in items if a["urgency"]["tier"] == tier.upper()]
    page = items[offset:offset + limit]
    return {"items": [_queue_item(a) for a in page], "total": len(items),
            "limit": limit, "offset": offset, **ALERTS_META}


@app.get("/alerts/{alert_id}")
def alert_detail(alert_id: str, x_correlation_id: str | None = Header(default=None)):
    corr = _corr(x_correlation_id)
    a = copy.deepcopy(_get_alert(alert_id, corr))
    a["case_id"] = CASE_BY_ALERT.get(alert_id)
    return a


@app.get("/alerts/{alert_id}/relationships")
def relationships(alert_id: str, x_correlation_id: str | None = Header(default=None)):
    corr = _corr(x_correlation_id)
    _get_alert(alert_id, corr)
    if alert_id == "ALT-2026-004417":
        return {"alert_id": alert_id,
                "paths": _load("casefile_hero.json")["entity_relationships"]}
    return {"alert_id": alert_id, "paths": []}


@app.post("/alerts/{alert_id}/case", status_code=201)
def get_or_create_case(alert_id: str,
                       idempotency_key: str | None = Header(default=None),
                       x_correlation_id: str | None = Header(default=None)):
    corr = _corr(x_correlation_id)
    _get_alert(alert_id, corr)
    if alert_id in CASE_BY_ALERT:
        return CASES[CASE_BY_ALERT[alert_id]]
    case_id = f"CASE-{len(CASES) + 1:04d}"
    case = {"case_id": case_id, "alert_id": alert_id, "status": "NEW",
            "assigned_team": "APJ-FinCrime-1", "region": ALERTS[alert_id]["region"],
            "created_at": _now(), "updated_at": _now()}
    CASES[case_id] = case
    CASE_BY_ALERT[alert_id] = case_id
    _audit(case_id, "CASE_CREATED", "system", "mock-backend", "case", case_id,
           {"alert_id": alert_id}, corr)
    return case


@app.post("/cases/{case_id}/assemble")
def assemble(case_id: str, x_correlation_id: str | None = Header(default=None)):
    corr = _corr(x_correlation_id)
    case = _get_case(case_id, corr)
    cf = _load("casefile_hero.json")
    now = _now()
    cf["case_id"] = case_id
    cf["assembled_at"] = now
    cf["case_file_id"] = f"CF-{case_id}-{len(CASE_FILES) + 1}"
    for cit in cf["source_provenance"]:
        cit["retrieved_at"] = now
    # Non-hero alerts get a thinned CaseFile so cases are visibly distinct and
    # the views are provably not hardcoded to the hero.
    if case["alert_id"] != "ALT-2026-004417":
        a = ALERTS[case["alert_id"]]
        cf["alert_details"].update({
            "alert_id": a["alert_id"], "alert_type": a["alert_type"],
            "alert_priority": a["alert_priority"], "status": a["status"],
            "created_at": a["created_at"], "sla_due_at": a["sla"]["due_at"],
            "rule_id": a["rule"]["rule_id"], "source_system": a["source_system"]})
        cf["priority_explanation"]["urgency"] = a["urgency"]
        cf["predictive_advisories"] = [a["advisory"]] if a.get("advisory") else []
        cf["customer_profile"] = {**a["company"],
                                   "industry": None, "kyc_expiry_date": None,
                                   "citation_ids": ["CIT-00003"]}
        cf["counterparty_profiles"] = []
        cf["transaction_timeline"] = []
        cf["entity_relationships"] = []
        cf["related_alerts"] = []
        cf["source_coverage"]["sections_populated"] = 7
        cf["missing_information"] = [{
            "field": "transaction_timeline",
            "reason": "Mock backend carries full evidence for the hero alert only",
            "severity": "info"}]
    CASE_FILES[case_id] = cf
    case["status"] = "ASSEMBLED"
    case["updated_at"] = _now()
    _audit(case_id, "CASEFILE_ASSEMBLED", "system", "mock-backend", "case_file",
           cf["case_file_id"],
           {"sections_populated": cf["source_coverage"]["sections_populated"]}, corr)
    return cf


@app.get("/cases/{case_id}")
def get_case(case_id: str, x_correlation_id: str | None = Header(default=None)):
    corr = _corr(x_correlation_id)
    case = _get_case(case_id, corr)
    drafts = DRAFTS.get(case_id, [])
    latest = drafts[-1] if drafts else None
    return {
        "case": case,
        "case_file": CASE_FILES.get(case_id),
        "latest_draft_meta": ({"draft_id": latest["draft_id"],
                               "draft_version": latest["draft_version"],
                               "created_at": latest["created_at"]} if latest else None),
        "workflow": None,            # B5 fills this
        "decisions": [],             # B3 fills this
    }


# ---- generation (B2) ------------------------------------------------------

def _result_payload(result, validation: dict) -> dict:
    return {
        "content": result.content,
        "sentences": [{"text": s.text, "citation_ids": s.citation_ids,
                       "kind": s.kind, "supported": s.supported}
                      for s in result.sentences],
        "generation": {"generation_id": result.generation_id,
                        "model_name": result.model_name,
                        "model_version": result.model_version,
                        "prompt_version": result.prompt_version,
                        "backend": result.backend,
                        "usage": result.usage,
                        "request_id": result.request_id},
        "validation": validation,
    }


def _generate_for_case(case_id: str, task: str, question: str | None, corr: str) -> dict:
    if case_id not in CASE_FILES:
        raise _err(422, "VALIDATION_FAILED", "Assemble the case file first", corr)
    case_file = CASE_FILES[case_id]
    generator = get_generator()
    try:
        result = generator.generate(task, case_file, question)
    except Exception:
        # Real backend unreachable mid-request -> honest fallback (§18).
        from trustsphere.generation.fallback import FallbackGenerator
        result = FallbackGenerator().generate(task, case_file, question)
    validation = validate(result, case_file)
    return _result_payload(result, validation)


class ExplainBody(BaseModel):
    question: str | None = None


@app.post("/cases/{case_id}/explanations")
def explain(case_id: str, body: ExplainBody | None = None,
            x_correlation_id: str | None = Header(default=None)):
    corr = _corr(x_correlation_id)
    _get_case(case_id, corr)
    payload = _generate_for_case(
        case_id, "explain", body.question if body else None, corr)
    payload["explanation_id"] = f"EXP-{case_id}-{uuid.uuid4().hex[:6]}"
    payload["case_id"] = case_id
    payload["created_at"] = _now()
    _audit(case_id, "EXPLANATION_GENERATED", "agent",
           payload["generation"]["backend"], "explanation",
           payload["explanation_id"],
           {"backend": payload["generation"]["backend"],
            "model": payload["generation"]["model_name"],
            "citation_coverage": payload["validation"]["citation_coverage"]}, corr)
    return payload


class DraftBody(BaseModel):
    mode: Literal["generate", "edit"]
    instruction: str | None = None
    content: str | None = None


@app.post("/cases/{case_id}/drafts", status_code=201)
def create_draft(case_id: str, body: DraftBody,
                 idempotency_key: str | None = Header(default=None),
                 x_correlation_id: str | None = Header(default=None)):
    corr = _corr(x_correlation_id)
    _get_case(case_id, corr)
    versions = DRAFTS.setdefault(case_id, [])
    version = len(versions) + 1
    if body.mode == "generate":
        payload = _generate_for_case(case_id, "narrative", body.instruction, corr)
        draft = {**payload, "created_by_type": "agent"}
    else:
        if not body.content or not body.content.strip():
            raise _err(422, "VALIDATION_FAILED", "content required for mode=edit", corr)
        draft = {"content": body.content, "sentences": [], "generation": None,
                 "validation": None, "created_by_type": "investigator"}
    draft.update({"draft_id": f"DRF-{case_id}-{version}", "case_id": case_id,
                  "draft_version": version, "verification_status": "unverified",
                  "created_at": _now()})
    versions.append(draft)
    _audit(case_id, "DRAFT_SAVED", draft["created_by_type"],
           (draft.get("generation") or {}).get("backend", "investigator.demo"),
           "draft", draft["draft_id"],
           {"version": version, "mode": body.mode}, corr)
    return draft


@app.get("/cases/{case_id}/drafts/latest")
def latest_draft(case_id: str, x_correlation_id: str | None = Header(default=None)):
    corr = _corr(x_correlation_id)
    _get_case(case_id, corr)
    versions = DRAFTS.get(case_id, [])
    if not versions:
        raise _err(404, "DRAFT_NOT_FOUND", "No draft for this case yet", corr)
    return versions[-1]


@app.get("/cases/{case_id}/audit-events")
def audit_events(case_id: str, x_correlation_id: str | None = Header(default=None)):
    corr = _corr(x_correlation_id)
    _get_case(case_id, corr)
    return {"items": AUDIT.get(case_id, [])}
