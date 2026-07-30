"""Exact-fact retrieval (CLAUDE.md §11 step 2: "Retrieve exact structured
facts with keyed SQL"). Thin adapter over `Repository` — the repository
returns raw typed rows (backend-agnostic dicts), this module turns them into
CaseFile subsections with citations attached. No calculation happens here:
every number is copied verbatim from the source row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from trustsphere.domain.cases import (
    AlertDetails,
    CounterpartyProfile,
    CustomerProfile,
    DataFreshness,
    RelatedAlertRef,
    TransactionTimelineEntry,
)
from trustsphere.domain.citations import Citation, EvidenceKind, MissingInformation, SourceType
from trustsphere.persistence.base import Repository


def _dt(v):
    if v is None:
        return None
    if isinstance(v, str):
        d = datetime.fromisoformat(v)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


class SqlFactRetriever:
    def __init__(self, repo: Repository, region: str):
        self.repo = repo
        self.region = region
        self.citations: list[Citation] = []
        self.missing: list[MissingInformation] = []
        self.freshness: list[DataFreshness] = []

    def _cite(
        self, source_type: SourceType, evidence_kind: EvidenceKind, source_id: str,
        source_locator: str, summary: str,
    ) -> str:
        cid = str(uuid.uuid4())
        self.citations.append(
            Citation(
                citation_id=cid, source_type=source_type, evidence_kind=evidence_kind,
                source_id=source_id, source_locator=source_locator,
                retrieved_at=datetime.now(timezone.utc), region=self.region, summary=summary,
            )
        )
        return cid

    def alert_details(self, alert_id: str) -> AlertDetails:
        a = self.repo.get_alert(alert_id)
        if a is None:
            self.missing.append(MissingInformation(field="alert_details", reason="RISK_ALERTS row not found"))
            return AlertDetails(
                alert_id=alert_id, alert_type=None, alert_priority=None, status=None,
                created_at=None, sla_due_at=None, company_id=None, transaction_id=None,
            )
        self._cite(SourceType.SQL_FACT, EvidenceKind.EXACT_FACT, alert_id, "RISK_ALERTS", f"Alert {alert_id} details")
        self.freshness.append(DataFreshness(source_object="RISK_ALERTS", retrieved_at=datetime.now(timezone.utc)))
        return AlertDetails(
            alert_id=a.alert_id, alert_type=a.alert_type, alert_priority=a.alert_priority,
            status=a.status, created_at=a.created_at, sla_due_at=a.sla_due_at,
            company_id=a.company_id, transaction_id=a.transaction_id,
        )

    def customer_profile(self, company_id: str | None) -> CustomerProfile | None:
        if not company_id:
            self.missing.append(MissingInformation(field="customer_profile", reason="alert has no COMPANY_ID"))
            return None
        row = self.repo.get_customer_profile(company_id)
        if row is None:
            self.missing.append(
                MissingInformation(field="customer_profile", reason=f"COMPANIES row not found for {company_id}",
                                    attempted_source="COMPANIES")
            )
            return None
        self._cite(SourceType.SQL_FACT, EvidenceKind.EXACT_FACT, company_id, "COMPANIES", f"Customer profile {company_id}")
        self.freshness.append(DataFreshness(source_object="COMPANIES", retrieved_at=datetime.now(timezone.utc)))
        return CustomerProfile(
            company_id=company_id,
            legal_name=row.get("LEGAL_NAME"),
            registration_number=row.get("REGISTRATION_NUMBER"),
            lei_code=row.get("LEI_CODE"),
            kyc_effective_status=row.get("KYC_EFFECTIVE_STATUS"),
            kyc_risk_rating=row.get("KYC_RISK_RATING"),
            client_segment=row.get("CLIENT_SEGMENT"),
            incorporation_country_id=row.get("INCORPORATION_COUNTRY_ID"),
            headquarters_country_id=row.get("HEADQUARTERS_COUNTRY_ID"),
        )

    def counterparty_profiles(self, alert_id: str) -> list[CounterpartyProfile]:
        rows = self.repo.get_counterparty_profiles(alert_id)
        out = []
        for r in rows:
            self._cite(
                SourceType.SQL_FACT, EvidenceKind.EXACT_FACT, r["counterparty_label"], "TRANSACTIONS",
                f"Counterparty on triggering transaction: {r['counterparty_label']}",
            )
            out.append(CounterpartyProfile(**r))
        if not out:
            self.missing.append(
                MissingInformation(field="counterparty_profiles", reason="no counterparty on the triggering transaction")
            )
        return out

    def transaction_timeline(self, company_id: str | None, limit: int = 25) -> list[TransactionTimelineEntry]:
        if not company_id:
            return []
        rows = self.repo.get_transaction_timeline(company_id, limit=limit)
        out = []
        for r in rows:
            self._cite(
                SourceType.SQL_FACT, EvidenceKind.EXACT_FACT, r["TRANSACTION_ID"], "TRANSACTIONS",
                f"Transaction {r['TRANSACTION_ID']} amount {r.get('AMOUNT_USD')}",
            )
            out.append(TransactionTimelineEntry(
                transaction_id=r["TRANSACTION_ID"],
                occurred_at=_dt(r.get("INITIATED_AT")),
                amount_usd=r.get("AMOUNT_USD"),
                currency_original=r.get("CURRENCY_ORIGINAL"),
                direction="OUTBOUND" if r.get("ORIGINATOR_COMPANY_ID") == company_id else "INBOUND",
                origin_country_id=r.get("ORIGINATING_COUNTRY_ID"),
                destination_country_id=r.get("DESTINATION_COUNTRY_ID"),
                is_cross_border=bool(r["IS_CROSS_BORDER"]) if r.get("IS_CROSS_BORDER") is not None else None,
            ))
        if not out:
            self.freshness.append(DataFreshness(source_object="TRANSACTIONS", retrieved_at=datetime.now(timezone.utc)))
        return out

    def related_alerts(self, alert_id: str, limit: int = 10) -> list[RelatedAlertRef]:
        rows = self.repo.get_related_alerts(alert_id, limit=limit)
        out = []
        for r in rows:
            self._cite(
                SourceType.SQL_FACT, EvidenceKind.EXACT_FACT, r["ALERT_ID"], "RISK_ALERTS",
                f"Related alert {r['ALERT_ID']} on same company",
            )
            out.append(RelatedAlertRef(
                alert_id=r["ALERT_ID"], alert_type=r.get("ALERT_TYPE"), status=r.get("STATUS"),
                shared_company_id=r.get("COMPANY_ID"),
            ))
        return out
