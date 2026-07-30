"""Deterministic fallback generator (CLAUDE.md §18).

Builds cited sentences directly from the CaseFile structure — no model call.
Targets the Track A CaseFile schema (src/trustsphere/domain/cases.py). Same
result shape as the orchestration backend so contracts are preserved;
`backend: "fallback"` lets the UI label it honestly. Must never raise: it is
the last line of defence when the live model path fails mid-request.
"""

from __future__ import annotations

import uuid

from .base import GenerationResult, Sentence, Task


def _fmt_usd(amount) -> str:
    try:
        whole, _, frac = str(amount).partition(".")
        return f"{int(whole):,}.{(frac or '00')[:2]} USD"
    except (ValueError, TypeError):
        return f"{amount} USD"


def _citation_index(case_file: dict) -> list[dict]:
    return case_file.get("source_provenance") or []


def _cite_by_locator(citations: list[dict], *needles: str) -> list[str]:
    """Citation ids whose source_locator mentions any needle (e.g. a
    transaction id). A's CaseFile carries citations centrally, not per
    section, so we resolve them by locator."""
    out = []
    for c in citations:
        locator = str(c.get("source_locator", ""))
        if any(n and str(n) in locator for n in needles):
            out.append(c["citation_id"])
    return out


def _build_sentences(case_file: dict) -> list[Sentence]:
    out: list[Sentence] = []
    cits = _citation_index(case_file)
    alert = case_file.get("alert_details") or {}
    pe = case_file.get("priority_explanation") or {}
    alert_id = alert.get("alert_id")

    if alert and pe:
        override = pe.get("hard_override_code")
        why = (f"a hard override ({override}) applies"
               if override else
               f"its urgency score is {pe.get('urgency_score')} under policy "
               f"{pe.get('policy_version')}")
        out.append(Sentence(
            text=(f"Alert {alert_id} ({alert.get('alert_type')}, source "
                  f"priority {alert.get('alert_priority')}) is ranked "
                  f"{pe.get('urgency_tier')} because {why}."),
            citation_ids=_cite_by_locator(cits, alert_id),
            kind="exact_fact"))

    profile = case_file.get("customer_profile") or {}
    if profile:
        out.append(Sentence(
            text=(f"The customer is {profile.get('legal_name')} "
                  f"({profile.get('company_id')}), KYC status "
                  f"{profile.get('kyc_effective_status')}, KYC risk rating "
                  f"{profile.get('kyc_risk_rating')}."),
            citation_ids=_cite_by_locator(cits, profile.get("company_id")),
            kind="exact_fact"))

    for txn in (case_file.get("transaction_timeline") or [])[:3]:
        out.append(Sentence(
            text=(f"On {txn.get('occurred_at')} transaction "
                  f"{txn.get('transaction_id')} moved "
                  f"{_fmt_usd(txn.get('amount_usd'))} "
                  f"({txn.get('direction')}) via "
                  f"{txn.get('origin_country_id')}→"
                  f"{txn.get('destination_country_id')}."),
            citation_ids=_cite_by_locator(cits, txn.get("transaction_id")),
            kind="exact_fact"))

    for edge in (case_file.get("entity_relationships") or [])[:4]:
        out.append(Sentence(
            text=(f"Graph evidence: {edge.get('source_node')} "
                  f"-{edge.get('relationship_type')}-> "
                  f"{edge.get('target_node')}."),
            citation_ids=[edge["citation_id"]] if edge.get("citation_id") else [],
            kind="relationship_inference"))

    for pc in (case_file.get("policy_context") or [])[:2]:
        out.append(Sentence(
            text=(f"{pc.get('document_id')} {pc.get('passage_locator')}: "
                  f"{pc.get('text')}"),
            citation_ids=[pc["citation_id"]] if pc.get("citation_id") else [],
            kind="policy_guidance"))

    for miss in case_file.get("missing_information") or []:
        out.append(Sentence(
            text=f"Missing information — {miss.get('field')}: {miss.get('reason')}.",
            citation_ids=[], kind="ai_synthesis"))

    if not out:
        out.append(Sentence(text="No evidence sections were present in the case file.",
                            citation_ids=[], kind="ai_synthesis"))
    return out


class FallbackGenerator:
    def generate(self, task: Task, case_file: dict,
                 question: str | None = None) -> GenerationResult:
        try:
            sentences = _build_sentences(case_file)
        except Exception:
            # Never let the fallback itself fail the request.
            sentences = [Sentence(
                text="Evidence rendering failed; consult the case file "
                     "directly.", citation_ids=[], kind="ai_synthesis")]
        return GenerationResult(
            task=task,
            sentences=sentences,
            backend="fallback",
            model_name="deterministic-fallback",
            model_version="0.2.0",
            prompt_version={"explain": "explain-1.1",
                            "narrative": "narrative-1.1"}[task],
            generation_id=f"GEN-{uuid.uuid4().hex[:8]}",
            usage=None,
            request_id=None,
        )
