"""Citation and evidence-kind types shared across retrieval and generation.

CLAUDE.md §11 "Citation contract": generated factual claims must reference
citation IDs, and the UI must distinguish exact fact / relationship inference
/ policy guidance / historical reference / AI-generated synthesis. These
types are the shared vocabulary so retrieval (Track A) and generation
(Track B) never disagree on what a citation means.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SourceType(StrEnum):
    SQL_FACT = "sql_fact"
    GRAPH_RELATIONSHIP = "graph_relationship"
    VECTOR_POLICY = "vector_policy"
    HISTORICAL_CASE = "historical_case"


class EvidenceKind(StrEnum):
    """How the UI must label a piece of evidence (CLAUDE.md §11)."""

    EXACT_FACT = "exact_fact"
    RELATIONSHIP_INFERENCE = "relationship_inference"
    POLICY_GUIDANCE = "policy_guidance"
    HISTORICAL_REFERENCE = "historical_reference"
    AI_SYNTHESIS = "ai_synthesis"


class Citation(BaseModel):
    """One evidence item with full provenance.

    Every factual item assembled into a CaseFile must carry this — no bare
    numbers or claims without a source identifier and retrieval timestamp
    (CLAUDE.md §8).
    """

    citation_id: str
    source_type: SourceType
    evidence_kind: EvidenceKind
    source_id: str = Field(description="Stable key in the source system, e.g. TRANSACTION_ID")
    source_locator: str = Field(description="Table/column or graph-path/document-passage locator")
    source_version: str | None = None
    retrieved_at: datetime
    region: str
    permission_scope: str = "case_team"
    summary: str = Field(description="Short human-readable statement of what this citation supports")


class MissingInformation(BaseModel):
    """An explicitly-declared gap. CLAUDE.md §8: never infer a missing value."""

    field: str
    reason: str
    attempted_source: str | None = None
