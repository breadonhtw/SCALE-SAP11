"""Minimal SAP AI Core client: OAuth + orchestration deployment discovery.

Talks to the documented AI API / orchestration v2 REST endpoints directly
(docs/sap-reference.md §2). We deliberately avoid sap-ai-sdk-gen on this dev
machine: its dependency tree pulls pandas, whose compiled DLLs are blocked by
the local Application Control policy. The REST contract is the same documented
API surface.

Credentials come from the team_11_credentials.json `ai_core` block, located
via env TEAM11_CREDS (same mechanism as scripts/*.py).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

DEFAULT_CREDS = Path.home() / "Desktop" / "SAP" / "team-11" / "team_11_credentials.json"
TIMEOUT = 60


class AICoreError(RuntimeError):
    """Stable app error wrapping AI Core failures (no secrets in message)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def load_ai_core_creds() -> dict:
    path = Path(os.environ.get("TEAM11_CREDS", str(DEFAULT_CREDS)))
    if not path.exists():
        raise AICoreError(f"Credentials file not found (TEAM11_CREDS): {path}")
    creds = json.loads(path.read_text(encoding="utf-8"))["ai_core"]
    return creds


class AICoreClient:
    def __init__(self, creds: dict | None = None):
        self.creds = creds or load_ai_core_creds()
        self._token: str | None = None
        self._token_expiry = 0.0

    def _auth_url(self) -> str:
        url = self.creds["auth_url"].rstrip("/")
        return url if url.endswith("/oauth/token") else url + "/oauth/token"

    def token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        resp = requests.post(
            self._auth_url(),
            data={"grant_type": "client_credentials"},
            auth=(self.creds["client_id"], self.creds["client_secret"]),
            timeout=TIMEOUT,
        )
        if not resp.ok:
            raise AICoreError(f"OAuth token request failed (HTTP {resp.status_code})",
                              resp.status_code)
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 3600))
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token()}",
                "AI-Resource-Group": self.creds["resource_group"],
                "Content-Type": "application/json"}

    def orchestration_deployment_url(self) -> str:
        api = self.creds["api_url"].rstrip("/")
        resp = requests.get(
            f"{api}/v2/lm/deployments",
            params={"scenarioId": "orchestration", "executableIds": "orchestration",
                    "status": "RUNNING"},
            headers=self._headers(), timeout=TIMEOUT)
        if not resp.ok:
            raise AICoreError(f"Deployment discovery failed (HTTP {resp.status_code})",
                              resp.status_code)
        resources = resp.json().get("resources", [])
        if not resources:
            raise AICoreError("No RUNNING orchestration deployment found")
        resources.sort(key=lambda r: r.get("startTime", ""), reverse=True)
        return resources[0]["deploymentUrl"]

    def v2_completion(self, config: dict, placeholder_values: dict,
                      messages_history: list[dict] | None = None) -> dict:
        url = self.orchestration_deployment_url().rstrip("/") + "/v2/completion"
        body: dict = {"config": config, "placeholder_values": placeholder_values}
        if messages_history:
            body["messages_history"] = messages_history
        resp = requests.post(url, json=body, headers=self._headers(), timeout=180)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            time.sleep(min(retry_after, 30))
            resp = requests.post(url, json=body, headers=self._headers(), timeout=180)
        if not resp.ok:
            try:
                err = resp.json().get("error")
                msg = (err or [{}])[0].get("message") if isinstance(err, list) \
                    else (err or {}).get("message", resp.text[:300])
            except ValueError:
                msg = resp.text[:300]
            raise AICoreError(f"Orchestration completion failed (HTTP "
                              f"{resp.status_code}): {msg}", resp.status_code)
        return resp.json()
