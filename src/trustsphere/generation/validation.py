"""Post-generation validation: citation coverage and numeric fidelity.

The LLM's output is never trusted directly (CLAUDE.md §11-12): every sentence
must cite known citation IDs, and every number it wrote must exist somewhere
in the CaseFile evidence after normalisation. Failures don't block the result;
they mark sentences unsupported so the UI can flag them visibly.
"""

from __future__ import annotations

import json
import re

from .base import GenerationResult

# Digits not embedded in identifier-like tokens (CIT-00017, ALT-2026-..., LEI…).
_NUM_RE = re.compile(r"(?<![\w\-./])\d[\d,]*(?:\.\d+)?(?![\w\-/])")


def _normalise(num: str) -> str:
    n = num.replace(",", "")
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n


def _numbers_in(text: str) -> set[str]:
    return {_normalise(m) for m in _NUM_RE.findall(text)}


def case_file_number_pool(case_file: dict) -> set[str]:
    # All numerals appearing anywhere in the evidence, normalised. A number in
    # generated text is "faithful" iff it appears in this pool.
    return _numbers_in(json.dumps(case_file, ensure_ascii=False))


def known_citation_ids(case_file: dict) -> set[str]:
    return {c["citation_id"] for c in case_file.get("source_provenance", [])}


def validate(result: GenerationResult, case_file: dict) -> dict:
    known = known_citation_ids(case_file)
    pool = case_file_number_pool(case_file)

    cited_sentences = 0
    numeric_mismatches = 0
    for s in result.sentences:
        valid_cits = [c for c in s.citation_ids if c in known]
        unknown_cits = [c for c in s.citation_ids if c not in known]
        if unknown_cits:
            # A fabricated citation invalidates the sentence's support.
            s.citation_ids = valid_cits
        bad_numbers = sorted(_numbers_in(s.text) - pool)
        if bad_numbers:
            numeric_mismatches += 1
        s.supported = bool(valid_cits) and not bad_numbers
        if not s.supported and s.kind != "ai_synthesis":
            s.kind = "ai_synthesis"
        if s.supported:
            cited_sentences += 1

    total = len(result.sentences) or 1
    return {
        "citation_coverage": round(cited_sentences / total, 3),
        "unsupported_sentences": total - cited_sentences,
        "numeric_mismatches": numeric_mismatches,
    }
