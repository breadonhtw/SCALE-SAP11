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
    cf = json.loads(
        (ROOT / "data" / "fixtures" / "casefile_hero.json").read_text(encoding="utf-8"))
    cf["case_id"] = "CASE-TEST"
    return cf


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
                               "citation_ids": ["CIT-1"]},
            "priority_explanation": {"urgency": {"score": 10, "tier": "LOW",
                                                   "policy_version": "p1"},
                                       "citation_ids": ["CIT-2"]},
            "source_provenance": [{"citation_id": "CIT-1"}, {"citation_id": "CIT-2"}]}
    result = FallbackGenerator().generate("explain", thin)
    assert result.sentences
    val = validate(result, thin)
    assert val["citation_coverage"] > 0
