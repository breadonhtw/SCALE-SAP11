"""HTTP client for the Track A backend contract (docs/api-contract.md).

The cockpit talks only through this module. Base URL from env
TRUSTSPHERE_API_BASE_URL (default the local backend). Every response carries
"backend" — surface it, never hardcode a backend label in the UI.
"""

from __future__ import annotations

import os
import uuid

import requests

BASE_URL = os.environ.get("TRUSTSPHERE_API_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = 30
GEN_TIMEOUT = 180  # generation calls run a live LLM


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code


def _handle(resp: requests.Response):
    if resp.ok:
        return resp.json()
    try:
        body = resp.json()
        code = body.get("error_code", "INTERNAL_ERROR")
        message = body.get("message", resp.text[:300])
    except ValueError:
        code, message = "INTERNAL_ERROR", resp.text[:300]
    raise ApiError(resp.status_code, code, message)


def _get(path: str, **params):
    return _handle(requests.get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT))


def _post(path: str, body: dict | None = None, idempotent: bool = False,
          timeout: int = TIMEOUT):
    headers = {}
    if idempotent:
        headers["Idempotency-Key"] = f"ui-{uuid.uuid4().hex}"
    return _handle(requests.post(f"{BASE_URL}{path}", json=body or {},
                                 headers=headers, timeout=timeout))


# -- alerts -----------------------------------------------------------------

def health() -> dict:
    return _get("/health")


def queue(limit: int = 200) -> dict:
    return _get("/alerts/queue", limit=limit)


def score_all() -> dict:
    return _post("/alerts/score-all", timeout=120)


def alert_detail(alert_id: str) -> dict:
    return _get(f"/alerts/{alert_id}")


def score_alert(alert_id: str) -> dict:
    return _post(f"/alerts/{alert_id}/score", idempotent=True)


def predict_sla(alert_id: str) -> dict:
    return _post(f"/alerts/{alert_id}/predict-sla", idempotent=True)


# -- cases ------------------------------------------------------------------

def create_case(alert_id: str) -> dict:
    return _post(f"/alerts/{alert_id}/cases")


def get_case(case_id: str) -> dict:
    return _get(f"/cases/{case_id}")


def assemble_case(case_id: str) -> dict:
    return _post(f"/cases/{case_id}/assemble", idempotent=True)


# -- generation (Track B endpoint) ------------------------------------------

def explain(case_id: str, question: str | None = None) -> dict:
    return _post(f"/cases/{case_id}/explanations",
                 {"question": question, "task": "explain"}, timeout=GEN_TIMEOUT)


def generate_narrative(case_id: str, question: str | None = None) -> dict:
    return _post(f"/cases/{case_id}/explanations",
                 {"question": question, "task": "narrative"}, timeout=GEN_TIMEOUT)


def save_draft_edit(case_id: str, content: str, edited_by: str = "human") -> dict:
    return _post(f"/cases/{case_id}/drafts", {
        "content": content,
        "generation_id": f"human-edit-{uuid.uuid4().hex[:8]}",
        "prompt_version": "n/a",
        "model_version": "n/a",
        "created_by_type": "human",
        "verification_status": "unverified",
    })


def latest_draft(case_id: str) -> dict:
    return _get(f"/cases/{case_id}/drafts/latest")


# -- review workflow --------------------------------------------------------

def start_review_workflow(case_id: str, draft_id: str | None = None,
                          senior_review: bool = False) -> dict:
    return _post(f"/cases/{case_id}/review-workflows",
                 {"draft_id": draft_id, "senior_review": senior_review},
                 idempotent=True)


def transition_workflow(case_id: str, status: str,
                        external_instance_id: str | None = None) -> dict:
    body: dict = {"status": status}
    if external_instance_id:
        body["external_instance_id"] = external_instance_id
    resp = requests.patch(f"{BASE_URL}/cases/{case_id}/review-workflows",
                          json=body, timeout=TIMEOUT)
    return _handle(resp)


# -- decisions / audit ------------------------------------------------------

def record_decision(case_id: str, decision_type: str, rationale: str,
                    decided_by: str, attested: bool) -> dict:
    return _post(f"/cases/{case_id}/decisions",
                 {"decision_type": decision_type, "rationale": rationale,
                  "decided_by": decided_by, "attested": attested},
                 idempotent=True)


def audit_events(case_id: str) -> dict:
    return _get(f"/cases/{case_id}/audit-events")
