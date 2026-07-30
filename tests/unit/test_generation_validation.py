"""Unit tests: citation coverage, numeric fidelity, fallback contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trustsphere.generation.base import GenerationResult, Sentence  # noqa: E402
from trustsphere.generation.fallback import FallbackGenerator  # noqa: E402
from trustsphere.generation.validation import validate  # noqa: E402


@pytest.fixture()
def case_file() -> dict:
    """Track A CaseFile shape (domain/cases.py), minimal but representative."""
    return {
        "case_id": "CASE-TEST",
        "alert_details": {"alert_id": "ALT-1", "alert_type": "SANCTIONS_SCREENING",
                           "alert_priority": "CRITICAL", "status": "OPEN"},
        "priority_explanation": {"urgency_score": 87.5, "urgency_tier": "CRITICAL",
                                   "hard_override_code": "SANCTIONS_MATCH",
                                   "complexity_band": "HIGH",
                                   "policy_version": "p-1"},
        "customer_profile": {"company_id": "CMP-1", "legal_name": "Meridian Ltd",
                              "kyc_effective_status": "EXPIRED",
                              "kyc_risk_rating": "HIGH"},
        "transaction_timeline": [
            {"transaction_id": "TXN-2026-118344", "occurred_at": "2026-07-21T09:30:00Z",
             "amount_usd": "1250000.00", "direction": "OUTBOUND",
             "origin_country_id": "SGP", "destination_country_id": "KYM"}],
        "entity_relationships": [
            {"relationship_type": "OWNS", "source_node": "OWN-2214",
             "target_node": "CMP-1", "citation_id": "CIT-00031"}],
        "policy_context": [
            {"document_id": "POL-AML-014", "passage_locator": "SEC-4.2",
             "text": "Confirmed sanctions matches escalate within one business day.",
             "similarity_score": 0.83, "citation_id": "CIT-00044"}],
        "missing_information": [
            {"field": "predictive_advisories", "reason": "no prediction yet"}],
        "source_provenance": [
            {"citation_id": "CIT-00001", "source_id": "RISK_ALERTS",
             "source_locator": "ALERT_ID=ALT-1"},
            {"citation_id": "CIT-00003", "source_id": "COMPANIES",
             "source_locator": "COMPANY_ID=CMP-1"},
            {"citation_id": "CIT-00017", "source_id": "TRANSACTIONS",
             "source_locator": "TRANSACTION_ID=TXN-2026-118344"},
            {"citation_id": "CIT-00031", "source_id": "GRAPH",
             "source_locator": "OWNS:OWN-2214->CMP-1"},
            {"citation_id": "CIT-00044", "source_id": "POLICY_PASSAGES",
             "source_locator": "POL-AML-014 SEC-4.2"}],
    }


def _result(sentences: list[Sentence]) -> GenerationResult:
    return GenerationResult(task="explain", sentences=sentences, backend="fallback",
                            model_name="t", model_version="t", prompt_version="t",
                            generation_id="GEN-test")


def test_supported_sentence_counts_toward_coverage(case_file):
    result = _result([Sentence(text="Payment of 1250000.00 USD was sent.",
                               citation_ids=["CIT-00017"], kind="exact_fact")])
    val = validate(result, case_file)
    assert val["citation_coverage"] == 1.0
    assert val["unsupported_sentences"] == 0
    assert result.sentences[0].supported is True


def test_unknown_citation_is_stripped_and_sentence_unsupported(case_file):
    result = _result([Sentence(text="A claim.", citation_ids=["CIT-99999"],
                               kind="exact_fact")])
    val = validate(result, case_file)
    assert result.sentences[0].citation_ids == []
    assert result.sentences[0].supported is False
    assert result.sentences[0].kind == "ai_synthesis"  # downgraded
    assert val["unsupported_sentences"] == 1


def test_number_not_in_evidence_flags_mismatch(case_file):
    result = _result([Sentence(text="The payment was 9999999.00 USD.",
                               citation_ids=["CIT-00017"], kind="exact_fact")])
    val = validate(result, case_file)
    assert val["numeric_mismatches"] == 1
    assert result.sentences[0].supported is False


def test_formatted_number_matches_after_normalisation(case_file):
    # 1,250,000.00 in prose vs 1250000.00 in evidence must match.
    result = _result([Sentence(text="An outbound payment of 1,250,000.00 was made.",
                               citation_ids=["CIT-00017"], kind="exact_fact")])
    val = validate(result, case_file)
    assert val["numeric_mismatches"] == 0
    assert result.sentences[0].supported is True


def test_identifier_digits_are_not_treated_as_numbers(case_file):
    # Digits inside TXN-2026-118344 / CIT ids must not trigger numeric checks.
    result = _result([Sentence(text="See transaction TXN-2026-118344.",
                               citation_ids=["CIT-00017"], kind="exact_fact")])
    assert validate(result, case_file)["numeric_mismatches"] == 0


def test_uncited_sentence_is_unsupported_but_not_a_mismatch(case_file):
    result = _result([Sentence(text="This pattern may indicate layering.",
                               citation_ids=[], kind="ai_synthesis")])
    val = validate(result, case_file)
    assert val["unsupported_sentences"] == 1
    assert val["numeric_mismatches"] == 0


def test_fallback_produces_valid_cited_result(case_file):
    result = FallbackGenerator().generate("narrative", case_file)
    assert result.backend == "fallback"
    assert result.sentences, "fallback must produce sentences"
    val = validate(result, case_file)
    # Fallback text is built from the evidence, so it must validate cleanly.
    assert val["numeric_mismatches"] == 0
    kinds = {s.kind for s in result.sentences}
    assert "exact_fact" in kinds and "relationship_inference" in kinds


def test_fallback_handles_thin_case_file():
    thin = {"alert_details": {"alert_id": "A-1", "alert_type": "X",
                               "alert_priority": "LOW"},
            "priority_explanation": {"urgency_score": 10,
                                       "urgency_tier": "LOW",
                                       "policy_version": "p1"},
            "source_provenance": [{"citation_id": "CIT-1",
                                    "source_id": "RISK_ALERTS",
                                    "source_locator": "ALERT_ID=A-1"}]}
    result = FallbackGenerator().generate("explain", thin)
    assert result.sentences
    val = validate(result, thin)
    assert val["citation_coverage"] > 0


def test_fallback_never_raises_on_garbage():
    result = FallbackGenerator().generate("narrative", {"transaction_timeline": "not-a-list"})
    assert result.backend == "fallback"
    assert result.sentences
