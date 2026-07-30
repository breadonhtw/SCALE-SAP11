"""Deterministic fallback generator (CLAUDE.md §18).

Builds cited sentences directly from the CaseFile structure — no model call.
Same result shape as the orchestration backend so contracts are preserved;
`backend: "fallback"` lets the UI label it honestly.
"""

from __future__ import annotations

import uuid

from .base import GenerationResult, Sentence, Task


def _fmt_usd(amount: str) -> str:
    whole, _, frac = amount.partition(".")
    return f"USD {int(whole):,}.{frac or '00'}"


def _build_sentences(case_file: dict) -> list[Sentence]:
    out: list[Sentence] = []
    alert = case_file.get("alert_details", {})
    urgency = case_file.get("priority_explanation", {}).get("urgency", {})
    if alert and urgency:
        override = urgency.get("hard_override_code")
        why = (f"a hard override ({override}) applies: {urgency.get('hard_override_reason')}"
               if override else
               f"its urgency score is {urgency.get('score')} under policy "
               f"{urgency.get('policy_version')}")
        out.append(Sentence(
            text=(f"Alert {alert.get('alert_id')} ({alert.get('alert_type')}) is ranked "
                  f"{urgency.get('tier')} because {why}."),
            citation_ids=(alert.get("citation_ids", []) +
                          case_file.get("priority_explanation", {}).get("citation_ids", [])),
            kind="exact_fact"))
    for txn in case_file.get("transaction_timeline", [])[:3]:
        out.append(Sentence(
            text=(f"On {txn['initiated_at']} transaction {txn['transaction_id']} sent "
                  f"{_fmt_usd(txn['amount_usd'])} ({txn['transaction_type']}) to "
                  f"{txn['beneficiary_company_id']} via "
                  f"{txn['originating_country']}→{txn['destination_country']}."),
            citation_ids=list(txn.get("citation_ids", [])), kind="exact_fact"))
    for cp in case_file.get("counterparty_profiles", []):
        if cp.get("sanctions_reference"):
            out.append(Sentence(
                text=(f"Counterparty {cp['legal_name']} ({cp['company_id']}) matches an "
                      f"active sanctions designation: {cp['sanctions_reference']}."),
                citation_ids=list(cp.get("citation_ids", [])), kind="exact_fact"))
    for path in case_file.get("entity_relationships", []):
        hops = " ; ".join(
            f"{e['source']} -{e['edge_type']}-> {e['target']}" for e in path["edges"])
        out.append(Sentence(
            text=f"Graph path {path['path_id']} ({path['derivation']}) links the entities: {hops}.",
            citation_ids=[path["citation_id"]], kind="relationship_inference"))
    for pc in case_file.get("policy_context", [])[:2]:
        out.append(Sentence(
            text=f"{pc['doc_id']} §{pc['clause_id']} ({pc['title']}): {pc['excerpt']}",
            citation_ids=list(pc.get("citation_ids", [])), kind="policy_guidance"))
    for miss in case_file.get("missing_information", []):
        out.append(Sentence(
            text=f"Missing information — {miss['field']}: {miss['reason']}.",
            citation_ids=[], kind="ai_synthesis"))
    if not out:
        out.append(Sentence(text="No evidence sections were present in the case file.",
                            citation_ids=[], kind="ai_synthesis"))
    return out


class FallbackGenerator:
    def generate(self, task: Task, case_file: dict,
                 question: str | None = None) -> GenerationResult:
        return GenerationResult(
            task=task,
            sentences=_build_sentences(case_file),
            backend="fallback",
            model_name="deterministic-fallback",
            model_version="0.1.0",
            prompt_version={"explain": "explain-1.0",
                            "narrative": "narrative-1.0"}[task],
            generation_id=f"GEN-{uuid.uuid4().hex[:8]}",
            usage=None,
            request_id=None,
        )
