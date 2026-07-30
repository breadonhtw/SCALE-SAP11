"""Investigation Assistant tools — thin wrappers over the Track A/B API.

The registry defines the agent's entire action space (CLAUDE.md §5): explain,
score, assemble, draft, start review. Dismissal, filing, blocking, source-data
edits, and decisions are not tools — they cannot be invoked no matter what the
model emits. A production Joule Studio skill would call these same endpoints.
"""

from __future__ import annotations

import json
import os

import requests

DEFAULT_BASE_URL = os.environ.get("TRUSTSPHERE_API_BASE_URL",
                                  "http://127.0.0.1:8000")
TIMEOUT = 60
GEN_TIMEOUT = 180

TOOL_DEFS: list[dict] = [
    {"type": "function", "function": {
        "name": "get_alert_details",
        "description": "Fetch one alert's stored facts plus its latest "
                       "deterministic urgency score and advisory SLA "
                       "prediction (null until computed).",
        "parameters": {"type": "object", "properties": {
            "alert_id": {"type": "string"}}, "required": ["alert_id"]},
    }},
    {"type": "function", "function": {
        "name": "calculate_regulatory_urgency",
        "description": "Run the deterministic, versioned urgency scoring "
                       "engine for an alert. Returns score, tier, hard "
                       "override, full factor breakdown with reason codes, "
                       "and complexity band.",
        "parameters": {"type": "object", "properties": {
            "alert_id": {"type": "string"}}, "required": ["alert_id"]},
    }},
    {"type": "function", "function": {
        "name": "assemble_case_file",
        "description": "Create/get the case for an ALERT and assemble the "
                       "typed CaseFile via HybridRAG (exact SQL facts, graph "
                       "relationship paths, vector policy passages, missing "
                       "information, citations). Use this before answering "
                       "evidence questions. Takes an ALERT id (ALERT-…), "
                       "never a case id (CASE-…). Returns the case_id to use "
                       "with case-level tools.",
        "parameters": {"type": "object", "properties": {
            "alert_id": {"type": "string",
                          "description": "Alert identifier, e.g. ALERT-9001"}},
            "required": ["alert_id"]},
    }},
    {"type": "function", "function": {
        "name": "draft_supporting_narrative",
        "description": "Generate and persist a cited supporting investigation "
                       "narrative for an existing case (AI Core generation "
                       "with citation/number validation). Takes the CASE id "
                       "(CASE-…). If you only have an alert id, call "
                       "assemble_case_file first. Returns validation metrics "
                       "and the persisted draft version.",
        "parameters": {"type": "object", "properties": {
            "case_id": {"type": "string",
                          "description": "Case identifier, e.g. CASE-ALERT-9001"}},
            "required": ["case_id"]},
    }},
    {"type": "function", "function": {
        "name": "start_human_review",
        "description": "Start the human review workflow for a case. Only a "
                       "human investigator can subsequently decide; this tool "
                       "only routes the case to them.",
        "parameters": {"type": "object", "properties": {
            "case_id": {"type": "string"},
            "draft_id": {"type": "string"}},
            "required": ["case_id"]},
    }},
]

TOOL_NAMES = {t["function"]["name"] for t in TOOL_DEFS}


def _compact_case_file(cf: dict) -> dict:
    """Trim the CaseFile for the model: keep facts + citation ids, drop
    verbose provenance metadata (the ids are what the answer must cite)."""
    return {
        "case_id": cf.get("case_id"),
        "alert_details": cf.get("alert_details"),
        "priority_explanation": cf.get("priority_explanation"),
        "predictive_advisories": cf.get("predictive_advisories"),
        "customer_profile": cf.get("customer_profile"),
        "counterparty_profiles": cf.get("counterparty_profiles"),
        "transaction_timeline": cf.get("transaction_timeline"),
        "entity_relationships": cf.get("entity_relationships"),
        "related_alerts": cf.get("related_alerts"),
        "policy_context": cf.get("policy_context"),
        "missing_information": cf.get("missing_information"),
        "source_provenance": [
            {"citation_id": c["citation_id"], "source_id": c["source_id"],
             "source_locator": c["source_locator"]}
            for c in cf.get("source_provenance", [])],
        "source_coverage": cf.get("source_coverage"),
    }


def execute_tool(name: str, args: dict,
                 base_url: str = DEFAULT_BASE_URL) -> dict:
    """Execute one tool. Always returns a dict; errors are returned as data
    so the model can react (never an exception up into the loop)."""
    try:
        if name == "get_alert_details":
            r = requests.get(f"{base_url}/alerts/{args['alert_id']}",
                             timeout=TIMEOUT)
            return r.json()
        if name == "calculate_regulatory_urgency":
            r = requests.post(f"{base_url}/alerts/{args['alert_id']}/score",
                              json={}, timeout=TIMEOUT)
            return r.json()
        if name == "assemble_case_file":
            if str(args.get("alert_id", "")).upper().startswith("CASE-"):
                return {"error": "assemble_case_file takes an ALERT id; you "
                                  "passed a case id. For an existing case use "
                                  "draft_supporting_narrative(case_id=…) or "
                                  "start_human_review(case_id=…) directly."}
            case = requests.post(f"{base_url}/alerts/{args['alert_id']}/cases",
                                 json={}, timeout=TIMEOUT).json()
            if "case" not in case:
                return case  # error payload from the backend
            case_id = case["case"]["CASE_ID"]
            assembled = requests.post(f"{base_url}/cases/{case_id}/assemble",
                                      json={}, timeout=TIMEOUT).json()
            if "case_file" not in assembled:
                return assembled
            return {"backend": assembled.get("backend"), "case_id": case_id,
                    "case_file": _compact_case_file(assembled["case_file"])}
        if name == "draft_supporting_narrative":
            r = requests.post(
                f"{base_url}/cases/{args['case_id']}/explanations",
                json={"task": "narrative"}, timeout=GEN_TIMEOUT)
            body = r.json()
            if "explanation" not in body:
                return body
            exp = body["explanation"]
            return {"backend": body.get("backend"),
                    "draft": {k: body["draft"][k] for k in
                              ("DRAFT_ID", "DRAFT_VERSION", "CREATED_AT")}
                    if body.get("draft") else None,
                    "validation": exp["validation"],
                    "sentences": exp["sentences"]}
        if name == "start_human_review":
            r = requests.post(
                f"{base_url}/cases/{args['case_id']}/review-workflows",
                json={"draft_id": args.get("draft_id"),
                      "senior_review": False}, timeout=TIMEOUT)
            return r.json()
        return {"error": f"Unknown tool {name!r}. Available: "
                         f"{sorted(TOOL_NAMES)}"}
    except requests.RequestException as exc:
        return {"error": f"Backend call failed: {type(exc).__name__}"}
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return {"error": f"Tool {name} failed: {type(exc).__name__}: {exc}"}
