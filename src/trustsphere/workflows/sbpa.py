"""SAP Build Process Automation client (B5).

Triggers the "TrustSphere Human Review" process via the SBPA Workflow API and
polls instance status/context back (docs/sap-reference.md §3; the
destination-based callback needs a public URL, so localhost polls).

Credentials: the raw BTP service key JSON downloaded from the cockpit
(`api`, `url`, `clientid`, `clientsecret`, optionally `api_key` added by
hand), path via env/setting SBPA_SERVICE_KEY — gitignored, never in the
repo. The environment-scoped `api-key` header is mandatory with
client-credentials auth (2025+ mechanism).

Known error meanings (sap-reference §3.6):
- 422 on start → context field names/casing don't match the trigger inputs.
- 403 → missing `api-key` header or missing scope on the key.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

TIMEOUT = 60


class SBPAError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _key_path() -> str:
    return os.environ.get("SBPA_SERVICE_KEY", "")


class SBPAClient:
    def __init__(self, service_key_path: str | None = None,
                 definition_id: str | None = None,
                 api_key: str | None = None,
                 trigger_url: str | None = None):
        path = service_key_path or _key_path()
        if not path or not Path(path).exists():
            raise SBPAError(f"SBPA service key file not found: {path!r}")
        key = json.loads(Path(path).read_text(encoding="utf-8"))
        self.api_base = str(key.get("api", "")).rstrip("/")
        self.auth_url = str(key.get("url", "")).rstrip("/")
        self.client_id = key.get("clientid", "")
        self.client_secret = key.get("clientsecret", "")
        self.api_key = api_key or key.get("api_key", "") \
            or os.environ.get("SAP_BUILD_API_KEY", "")
        self.definition_id = definition_id \
            or os.environ.get("SAP_BUILD_WORKFLOW_DEFINITION_ID", "")
        # Exact trigger URL from the Control Tower "View" dialog wins over
        # any guessed path (settles the /public prefix ambiguity).
        self.trigger_url = trigger_url \
            or os.environ.get("SAP_BUILD_TRIGGER_URL", "")
        self._token: str | None = None
        self._token_expiry = 0.0

    @classmethod
    def from_settings(cls, settings) -> "SBPAClient":
        return cls(service_key_path=settings.sbpa_service_key or None,
                   definition_id=settings.sap_build_workflow_definition_id or None,
                   api_key=settings.sap_build_api_key or None,
                   trigger_url=settings.sap_build_trigger_url or None)

    @staticmethod
    def configured(settings=None) -> bool:
        if settings is not None:
            path = settings.sbpa_service_key or _key_path()
            definition = (settings.sap_build_workflow_definition_id
                          or os.environ.get("SAP_BUILD_WORKFLOW_DEFINITION_ID"))
        else:
            path = _key_path()
            definition = os.environ.get("SAP_BUILD_WORKFLOW_DEFINITION_ID")
        return bool(path and Path(path).exists() and definition)

    # -- auth ----------------------------------------------------------------

    def token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        resp = requests.post(
            f"{self.auth_url}/oauth/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=TIMEOUT,
        )
        if not resp.ok:
            raise SBPAError(f"SBPA OAuth failed (HTTP {resp.status_code})",
                            resp.status_code)
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 3600))
        return self._token

    def _headers(self) -> dict:
        headers = {"Authorization": f"Bearer {self.token()}",
                   "Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        return headers

    def _instance_bases(self) -> list[str]:
        # Both path forms appear in SAP material; try /public first.
        return [f"{self.api_base}/public/workflow/rest/v1",
                f"{self.api_base}/workflow/rest/v1"]

    # -- operations ----------------------------------------------------------

    def start_instance(self, context: dict) -> dict:
        if not self.definition_id and not self.trigger_url:
            raise SBPAError("No SAP_BUILD_WORKFLOW_DEFINITION_ID / trigger URL configured")
        body = {"definitionId": self.definition_id, "context": context}
        urls = ([self.trigger_url] if self.trigger_url else
                [f"{b}/workflow-instances" for b in self._instance_bases()])
        last: requests.Response | None = None
        for url in urls:
            resp = requests.post(url, json=body, headers=self._headers(),
                                 timeout=TIMEOUT)
            if resp.status_code == 404 and url != urls[-1]:
                last = resp
                continue
            last = resp
            break
        assert last is not None
        if last.status_code in (200, 201):
            return last.json()
        if last.status_code == 422:
            raise SBPAError(
                "SBPA rejected the start payload (422) — context field names/"
                f"casing must exactly match the trigger inputs: {last.text[:200]}",
                422)
        if last.status_code == 403:
            raise SBPAError(
                "SBPA 403 — missing/invalid environment api-key header or "
                "missing scope on the key", 403)
        raise SBPAError(f"SBPA start failed (HTTP {last.status_code}): "
                        f"{last.text[:200]}", last.status_code)

    def _get(self, suffix: str) -> dict:
        last: requests.Response | None = None
        for base in self._instance_bases():
            resp = requests.get(f"{base}/{suffix}", headers=self._headers(),
                                timeout=TIMEOUT)
            last = resp
            if resp.status_code != 404:
                break
        assert last is not None
        if not last.ok:
            raise SBPAError(f"SBPA GET {suffix} failed (HTTP {last.status_code})",
                            last.status_code)
        return last.json()

    def get_instance(self, instance_id: str) -> dict:
        return self._get(f"workflow-instances/{instance_id}")

    def get_instance_context(self, instance_id: str) -> dict:
        return self._get(f"workflow-instances/{instance_id}/context")


def map_outcome(sbpa_status: str, context: dict | None) -> str:
    """Map an SBPA instance state (+ final context) to our WorkflowStatus.

    A COMPLETED instance whose outcome cannot be recognised maps to
    IN_REVIEW deliberately — guessing APPROVED for a regulated action would
    be worse than asking the human to check (the raw context lands in the
    audit event for diagnosis).
    """
    status = (sbpa_status or "").upper()
    if status in ("RUNNING", "SUSPENDED"):
        return "IN_REVIEW"
    if status not in ("COMPLETED",):
        return "IN_REVIEW"

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield str(k).lower()
                yield from _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from _walk(v)
        else:
            yield str(obj).lower()

    tokens = list(_walk(context or {}))
    joined = " ".join(tokens)
    # "approved: false" style booleans first
    if isinstance(context, dict):
        for k, v in context.items():
            if "approv" in str(k).lower() and isinstance(v, bool):
                return "APPROVED" if v else "RETURNED"
    if "reject" in joined or "return" in joined:
        return "RETURNED"
    if "request_information" in joined or "info" in joined:
        return "INFO_REQUESTED"
    if "approv" in joined or "escalat" in joined:
        return "APPROVED"
    return "IN_REVIEW"
