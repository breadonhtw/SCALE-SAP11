"""Shared pytest fixtures for Track A.

Unit tests exercise the scoring engine directly (no DB, no HTTP — CLAUDE.md
§25 "no DB, no HTTP" is the whole point of a pure scoring function).
Integration tests spin up a throwaway `LocalSQLiteRepository` per test in
`tmp_path`, never the real `data/local_trustsphere.db`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trustsphere.domain.alerts import AlertFactorInputs, ComplexityInputs
from trustsphere.scoring.policy import load_policy

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def policy():
    """The real, versioned config/scoring_policy.yaml — tests exercise the
    actual production policy, not a hand-rolled test double, so a change to
    the policy file that breaks an assumption is caught here.
    """
    load_policy.cache_clear()
    return load_policy()


@pytest.fixture
def base_inputs() -> AlertFactorInputs:
    """A fully-resolved, no-override, mid-urgency baseline. Individual tests
    call `.model_copy(update={...})` to isolate the one field they exercise.
    """
    return AlertFactorInputs(
        alert_id="ALERT-TEST-0001",
        alert_priority="MEDIUM",
        alert_type="UNUSUAL_TRANSACTION_VELOCITY",
        composite_risk_score=Decimal("50"),
        kyc_effective_status="VERIFIED",
        country_risk_score=Decimal("40"),
        risk_tier="MEDIUM",
        is_cross_border=False,
        created_at=NOW - timedelta(hours=10),
        sla_due_at=NOW + timedelta(hours=60),
        as_of=NOW,
        hours_remaining_to_sla=60.0,
        amount_usd=Decimal("20000"),
        baseline_avg_amount_usd=Decimal("18000"),
        sanctions_match=False,
        terrorist_financing_flag=False,
        restricted_customer_flag=False,
        has_prior_escalated_case=False,
        unresolved_fields=[],
    )


@pytest.fixture
def base_complexity() -> ComplexityInputs:
    return ComplexityInputs(
        alert_id="ALERT-TEST-0001",
        entity_count=2,
        jurisdiction_count=1,
        missing_kyc_count=0,
        source_system_count=1,
        related_alert_count=0,
        transaction_volume_band=0,
    )
