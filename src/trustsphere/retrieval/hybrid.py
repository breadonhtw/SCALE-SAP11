"""HybridRAG orchestration (CLAUDE.md §11): exact facts + relationships +
policy context, deduplicated and assembled into the typed CaseFile. This is
the one function the `POST /cases/{id}/assemble` endpoint calls.

Retrieval order followed exactly per CLAUDE.md §11:
1. (caller has already authorised the user / resolved region)
2. exact structured facts (SqlFactRetriever)
3. relationship paths (GraphRetriever)
4. policy context (VectorRetriever)
5. historical references — out of scope for this pass (ENABLE_HISTORICAL_CASE_RETRIEVAL
   defaults False; CLAUDE.md non-goal risk if built without permission filtering)
6. dedupe/rank — citations are already scoped per source, no cross-source dupes possible here
7. assemble CaseFile
8/9. generation + validation are Track B's job (POST /cases/{id}/explanations, /drafts)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from trustsphere.domain.alerts import ComplexityBand, UrgencyTier
from trustsphere.domain.cases import CaseFile, PriorityExplanation
from trustsphere.domain.citations import Citation, MissingInformation
from trustsphere.persistence.base import Repository
from trustsphere.retrieval.graph import GraphRetriever
from trustsphere.retrieval.sql import SqlFactRetriever
from trustsphere.retrieval.vector import VectorRetriever
from trustsphere.scoring.policy import ScoringPolicy


def assemble_case_file(
    repo: Repository,
    alert_id: str,
    case_id: str,
    region: str,
    policy: ScoringPolicy,
    vector_backend: str = "hana",
) -> CaseFile:
    now = datetime.now(timezone.utc)

    sql_r = SqlFactRetriever(repo, region)
    graph_r = GraphRetriever(repo, region)
    vector_r = VectorRetriever(repo, region, vector_backend=vector_backend)

    alert_details = sql_r.alert_details(alert_id)
    customer_profile = sql_r.customer_profile(alert_details.company_id)
    counterparty_profiles = sql_r.counterparty_profiles(alert_id)
    transaction_timeline = sql_r.transaction_timeline(alert_details.company_id)
    related_alerts = sql_r.related_alerts(alert_id)
    entity_relationships = graph_r.relationship_path(alert_id, alert_details.company_id)

    query_text = f"{alert_details.alert_type or 'unknown typology'} monitoring rule guidance and missing-information handling"
    policy_context = vector_r.search(query_text, limit=3)

    score = repo.get_latest_score(alert_id)
    missing: list[MissingInformation] = list(sql_r.missing)
    if score is None:
        missing.append(MissingInformation(
            field="priority_explanation", reason="alert has not been scored yet — call POST /alerts/{id}/score first"
        ))
        priority_explanation = PriorityExplanation(
            urgency_score=0.0, urgency_tier=UrgencyTier.LOW, hard_override_code=None,
            factor_breakdown=[], complexity_band=ComplexityBand.LOW, policy_version=policy.policy_version,
        )
    else:
        priority_explanation = PriorityExplanation(
            urgency_score=score.urgency_score, urgency_tier=score.urgency_tier,
            hard_override_code=score.hard_override.code if score.hard_override else None,
            factor_breakdown=[f.model_dump() for f in score.factors],
            complexity_band=score.complexity_band, policy_version=score.policy_version,
        )

    predictive_advisories = []
    pred = repo.get_latest_predictive_score(alert_id)
    if pred:
        from trustsphere.domain.cases import PredictiveAdvisory

        predictive_advisories.append(PredictiveAdvisory(
            prediction_type=pred["PREDICTION_TYPE"], prediction_value=pred["PREDICTION_VALUE"],
            model_name=pred["MODEL_NAME"], model_version=pred["MODEL_VERSION"],
            scored_at=pred["SCORED_AT"] if not isinstance(pred["SCORED_AT"], str) else datetime.fromisoformat(pred["SCORED_AT"]),
        ))
    else:
        missing.append(MissingInformation(
            field="predictive_advisories", reason="no SLA prediction yet — call POST /alerts/{id}/predict-sla first"
        ))

    all_citations: list[Citation] = sql_r.citations + graph_r.citations + vector_r.citations

    total_sections = 8  # alert, customer, counterparty, timeline, related, relationships, policy, predictive
    filled_sections = sum([
        1 if alert_details.company_id else 0,
        1 if customer_profile else 0,
        1 if counterparty_profiles else 0,
        1 if transaction_timeline else 0,
        1,  # related_alerts always attempted, empty list is a valid answer
        1 if entity_relationships else 0,
        1 if policy_context else 0,
        1 if predictive_advisories else 0,
    ])
    source_coverage = round(filled_sections / total_sections, 2)

    case_file = CaseFile(
        case_file_id=str(uuid.uuid4()),
        case_id=case_id,
        assembled_at=now,
        alert_details=alert_details,
        priority_explanation=priority_explanation,
        predictive_advisories=predictive_advisories,
        customer_profile=customer_profile,
        counterparty_profiles=counterparty_profiles,
        transaction_timeline=transaction_timeline,
        entity_relationships=entity_relationships,
        related_alerts=related_alerts,
        policy_context=policy_context,
        historical_case_references=[],
        missing_information=missing,
        source_provenance=all_citations,
        data_freshness=sql_r.freshness,
        source_coverage=source_coverage,
        region=region,
    )
    return case_file
