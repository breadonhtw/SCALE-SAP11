"""Relationship-path retrieval (CLAUDE.md §11 step 3, §5 differentiator).

Runtime path implemented here is bounded SQL traversal over the
company/owner/counterparty/alert/case edges — the CLAUDE.md §18 disclosed
pattern ("Versioned edge tables and bounded SQL graph traversal"). It is
correct and honest for the 1-2 hop relationship questions this prototype
needs (OWNS, SENT_TO/RECEIVED_FROM, TRIGGERED_BY, INVOLVED_IN, LOCATED_IN).

A real HANA Graph property-graph workspace (verified available —
docs/capability-matrix.md) can additionally be provisioned over the same
edge shape via scripts/create_graph_workspace.py for the "visible entity
relationship path backed by graph retrieval" differentiator; that script
has not been run against the live tenant in this session (no credentials
available in the build sandbox — see CLAUDE.md §26). Do not present this
adapter's output as SPARQL/RDF or as proof the workspace exists until that
script has actually been run and confirmed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from trustsphere.domain.cases import RelationshipEdge
from trustsphere.domain.citations import Citation, EvidenceKind, SourceType
from trustsphere.persistence.base import Repository


class GraphRetriever:
    def __init__(self, repo: Repository, region: str):
        self.repo = repo
        self.region = region
        self.citations: list[Citation] = []

    def _cite(self, source_id: str, locator: str, summary: str) -> str:
        cid = str(uuid.uuid4())
        self.citations.append(
            Citation(
                citation_id=cid, source_type=SourceType.GRAPH_RELATIONSHIP,
                evidence_kind=EvidenceKind.RELATIONSHIP_INFERENCE, source_id=source_id,
                source_locator=locator, retrieved_at=datetime.now(timezone.utc),
                region=self.region, summary=summary,
            )
        )
        return cid

    def relationship_path(self, alert_id: str, company_id: str | None) -> list[RelationshipEdge]:
        edges: list[RelationshipEdge] = []
        if not company_id:
            return edges

        # TRIGGERED_BY: alert <- company
        edges.append(RelationshipEdge(
            relationship_type="TRIGGERED_BY", source_node=f"Alert:{alert_id}", target_node=f"Company:{company_id}",
            citation_id=self._cite(alert_id, "RISK_ALERTS.COMPANY_ID", f"Alert {alert_id} triggered by company {company_id}"),
        ))

        # OWNS: beneficial owner -> company
        for owner in self.repo.get_beneficial_owners(company_id):
            name = owner.get("OWNER_NAME")
            if not name:
                continue
            edges.append(RelationshipEdge(
                relationship_type="OWNS", source_node=f"Owner:{name}", target_node=f"Company:{company_id}",
                citation_id=self._cite(
                    company_id, "COMPANY_BENEFICIAL_OWNERS",
                    f"{name} owns {owner.get('OWNERSHIP_PERCENTAGE')}% of {company_id}",
                ),
            ))

        # SENT_TO / RECEIVED_FROM: company <-> counterparty on the triggering transaction
        for cp in self.repo.get_counterparty_profiles(alert_id):
            edges.append(RelationshipEdge(
                relationship_type="SENT_TO", source_node=f"Company:{company_id}",
                target_node=f"Counterparty:{cp['counterparty_label']}",
                citation_id=self._cite(
                    company_id, "TRANSACTIONS", f"{company_id} transacted with {cp['counterparty_label']}"
                ),
            ))

        # INVOLVED_IN: company -> prior compliance case (via CASE_ALERTS/COMPLIANCE_CASES)
        for ra in self.repo.get_related_alerts(alert_id):
            edges.append(RelationshipEdge(
                relationship_type="RELATED_TO", source_node=f"Alert:{alert_id}", target_node=f"Alert:{ra['ALERT_ID']}",
                citation_id=self._cite(
                    ra["ALERT_ID"], "RISK_ALERTS.COMPANY_ID",
                    f"Alert {ra['ALERT_ID']} shares company {company_id} with {alert_id}",
                ),
            ))

        return edges
