"""Thin HTTP client for the backend (B1 scope).

PROVISIONAL SHAPES — mirrors mock_api/app.py until Person A publishes
docs/api-contract.md; reconcile both files against it then. The cockpit talks
only through this module, so swapping mock -> real backend is a base-URL
change (env TRUSTSPHERE_API_BASE_URL).
"""

from __future__ import annotations

import os
import uuid

import requests

BASE_URL = os.environ.get("TRUSTSPHERE_API_BASE_URL", "http://127.0.0.1:8000")
TIMEOUT = 15


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code


def _handle(resp: requests.Response):
    if resp.ok:
        return resp.json()
    try:
        err = resp.json()["error"]
    except (ValueError, KeyError):
        raise ApiError(resp.status_code, "INTERNAL", resp.text[:300])
    raise ApiError(resp.status_code, err.get("code", "INTERNAL"),
                   err.get("message", "unknown error"))


def _get(path: str, **params):
    return _handle(requests.get(f"{BASE_URL}{path}", params=params, timeout=TIMEOUT))


def _post(path: str, body: dict | None = None, idempotent: bool = False):
    headers = {"X-Correlation-Id": f"ui-{uuid.uuid4().hex[:12]}"}
    if idempotent:
        headers["Idempotency-Key"] = f"ui-{uuid.uuid4().hex}"
    return _handle(requests.post(f"{BASE_URL}{path}", json=body,
                                 headers=headers, timeout=TIMEOUT))


def health() -> dict:
    return _get("/health")


def list_alerts(status: str = "open", limit: int = 50) -> dict:
    return _get("/alerts", status=status, limit=limit)


def alert_detail(alert_id: str) -> dict:
    return _get(f"/alerts/{alert_id}")


def relationships(alert_id: str) -> dict:
    return _get(f"/alerts/{alert_id}/relationships")


def get_or_create_case(alert_id: str) -> dict:
    return _post(f"/alerts/{alert_id}/case", idempotent=True)


def assemble_case(case_id: str) -> dict:
    return _post(f"/cases/{case_id}/assemble", idempotent=True)


def get_case(case_id: str) -> dict:
    return _get(f"/cases/{case_id}")


def explain(case_id: str, question: str | None = None) -> dict:
    return _post(f"/cases/{case_id}/explanations",
                 {"question": question} if question else {})


def generate_draft(case_id: str, instruction: str | None = None) -> dict:
    return _post(f"/cases/{case_id}/drafts",
                 {"mode": "generate", "instruction": instruction}, idempotent=True)


def save_draft_edit(case_id: str, content: str) -> dict:
    return _post(f"/cases/{case_id}/drafts",
                 {"mode": "edit", "content": content}, idempotent=True)


def latest_draft(case_id: str) -> dict:
    return _get(f"/cases/{case_id}/drafts/latest")


def audit_events(case_id: str) -> dict:
    return _get(f"/cases/{case_id}/audit-events")
