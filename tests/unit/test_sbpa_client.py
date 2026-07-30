"""SBPA client tests (mocked requests, no tenant)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trustsphere.workflows.sbpa import SBPAClient, SBPAError, map_outcome  # noqa: E402


@pytest.fixture
def key_file(tmp_path) -> str:
    path = tmp_path / "sbpa_service_key.json"
    path.write_text(json.dumps({
        "api": "https://spa-api.example.com",
        "url": "https://auth.example.com",
        "clientid": "cid", "clientsecret": "secret", "api_key": "env-key-1",
    }), encoding="utf-8")
    return str(path)


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text or json.dumps(self._body)
        self.ok = status_code < 400

    def json(self):
        return self._body


def _client(key_file) -> SBPAClient:
    c = SBPAClient(service_key_path=key_file, definition_id="def-123")
    c._token = "tok"        # skip OAuth in unit tests
    c._token_expiry = 2**31
    return c


def test_start_instance_payload_and_api_key_header(key_file, monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse(201, {"id": "sbpa-inst-1", "status": "RUNNING"})

    monkeypatch.setattr("trustsphere.workflows.sbpa.requests.post", fake_post)
    result = _client(key_file).start_instance({"case_id": "CASE-1",
                                               "draft_id": "D-1",
                                               "evidence_summary": "s"})
    assert result["id"] == "sbpa-inst-1"
    call = calls[0]
    assert call["json"]["definitionId"] == "def-123"
    assert call["json"]["context"]["case_id"] == "CASE-1"
    assert call["headers"]["api-key"] == "env-key-1"
    assert call["url"].endswith("/public/workflow/rest/v1/workflow-instances")


def test_start_instance_falls_back_to_non_public_path_on_404(key_file, monkeypatch):
    responses = [FakeResponse(404), FakeResponse(201, {"id": "x"})]
    urls = []

    def fake_post(url, **kw):
        urls.append(url)
        return responses.pop(0)

    monkeypatch.setattr("trustsphere.workflows.sbpa.requests.post", fake_post)
    assert _client(key_file).start_instance({})["id"] == "x"
    assert "/public/" in urls[0] and "/public/" not in urls[1]


@pytest.mark.parametrize("status,fragment", [
    (422, "context field names"),
    (403, "api-key"),
])
def test_start_instance_typed_errors(key_file, monkeypatch, status, fragment):
    monkeypatch.setattr("trustsphere.workflows.sbpa.requests.post",
                        lambda *a, **k: FakeResponse(status, {}, "boom"))
    with pytest.raises(SBPAError) as err:
        _client(key_file).start_instance({})
    assert err.value.status == status
    assert fragment in str(err.value)


def test_explicit_trigger_url_wins(key_file, monkeypatch):
    urls = []
    monkeypatch.setattr(
        "trustsphere.workflows.sbpa.requests.post",
        lambda url, **kw: (urls.append(url), FakeResponse(201, {"id": "y"}))[1])
    c = SBPAClient(service_key_path=key_file, definition_id="def-123",
                   trigger_url="https://exact.example.com/trigger")
    c._token, c._token_expiry = "tok", 2**31
    c.start_instance({})
    assert urls == ["https://exact.example.com/trigger"]


def test_missing_key_file_raises():
    with pytest.raises(SBPAError):
        SBPAClient(service_key_path="C:/does/not/exist.json")


@pytest.mark.parametrize("sbpa_status,context,expected", [
    ("RUNNING", None, "IN_REVIEW"),
    ("SUSPENDED", None, "IN_REVIEW"),
    ("COMPLETED", {"approved": True}, "APPROVED"),
    ("COMPLETED", {"approved": False}, "RETURNED"),
    ("COMPLETED", {"decision": "Approve for escalation"}, "APPROVED"),
    ("COMPLETED", {"decision": "Return for edit"}, "RETURNED"),
    ("COMPLETED", {"requested_action": "request_information"}, "INFO_REQUESTED"),
    ("COMPLETED", {"unrelated": "data"}, "IN_REVIEW"),  # never guess APPROVED
])
def test_map_outcome(sbpa_status, context, expected):
    assert map_outcome(sbpa_status, context) == expected


def test_configured_false_without_env(monkeypatch):
    monkeypatch.delenv("SBPA_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SAP_BUILD_WORKFLOW_DEFINITION_ID", raising=False)
    assert SBPAClient.configured() is False
