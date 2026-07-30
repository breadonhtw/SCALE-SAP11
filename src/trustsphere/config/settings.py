"""Typed configuration loaded from environment variables / .env.

Follows CLAUDE.md §24. Never logs credentials — `__repr__` on Settings is the
default pydantic one, which does print field values, so callers must not log
a raw Settings object; use `Settings.safe_dict()` instead.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

_SECRET_FIELDS = {
    "hana_password",
    "sap_ai_core_client_secret",
    "sap_build_client_secret",
    "audit_hashing_key",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    default_region: str = "APJ"
    display_timezone: str = "Asia/Singapore"

    # local -> SQLite fallback, hana -> SAP HANA Cloud TEAM_11_USER
    data_backend: str = "local"
    local_db_path: str = str(REPO_ROOT / "data" / "local_trustsphere.db")

    team11_creds: str = ""
    hana_host: str = ""
    hana_port: int = 443
    hana_user: str = ""
    hana_password: str = ""
    hana_encrypt: bool = True
    hana_schema: str = "TEAM_11_USER"
    hana_reference_schema: str = "TRUSTSPHERE_REFERENCE"

    vector_backend: str = "hana"
    graph_backend: str = "hana"
    prediction_backend: str = "local_oss"
    generation_backend: str = "sap_ai_core"
    workflow_backend: str = "sap_build"

    sap_ai_core_client_id: str = ""
    sap_ai_core_client_secret: str = ""
    sap_ai_core_token_url: str = ""
    sap_ai_core_base_url: str = ""
    sap_ai_core_resource_group: str = "team-11"
    sap_ai_model_deployment_id: str = ""

    sap_build_api_base_url: str = ""
    sap_build_workflow_definition_id: str = ""
    sap_build_client_id: str = ""
    sap_build_client_secret: str = ""
    sap_build_token_url: str = ""

    audit_hashing_key: str = ""
    case_data_region: str = "APJ"
    allow_synthetic_data_only: bool = False
    enable_historical_case_retrieval: bool = False

    scoring_policy_path: str = str(REPO_ROOT / "config" / "scoring_policy.yaml")
    feature_schema_path: str = str(REPO_ROOT / "config" / "feature_schema.yaml")

    @field_validator("data_backend")
    @classmethod
    def _validate_backend(cls, v: str) -> str:
        allowed = {"local", "hana"}
        if v not in allowed:
            raise ValueError(f"data_backend must be one of {allowed}, got {v!r}")
        return v

    def safe_dict(self) -> dict:
        """Dict representation with secrets redacted — use this for logging."""
        d = self.model_dump()
        for key in _SECRET_FIELDS:
            if d.get(key):
                d[key] = "***REDACTED***"
        return d


@lru_cache
def get_settings() -> Settings:
    return Settings()
