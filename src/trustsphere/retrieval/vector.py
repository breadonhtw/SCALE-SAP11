"""Policy/rule semantic retrieval (CLAUDE.md §11 step 4, §5 HANA vector engine).

Two paths, selected by `Settings.vector_backend` and what the active
Repository actually is:

- HANA-native: `HanaRepository.search_policy_passages_native` — in-DB
  VECTOR_EMBEDDING + COSINE_SIMILARITY (verified capability, see
  docs/capability-matrix.md). Requires POLICY_PASSAGES.EMBEDDING to be
  populated via scripts/load_policy_corpus.py first.
- Local fallback: scikit-learn TF-IDF + cosine similarity over
  `Repository.list_policy_passages()`. CLAUDE.md §18: "Do not claim HANA
  vector execution" when this path runs — `used_backend` on the result
  says which one actually served the query.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from trustsphere.domain.cases import PolicyPassage
from trustsphere.domain.citations import Citation, EvidenceKind, SourceType
from trustsphere.persistence.base import Repository


class VectorRetriever:
    def __init__(self, repo: Repository, region: str, vector_backend: str = "hana"):
        self.repo = repo
        self.region = region
        self.vector_backend = vector_backend
        self.citations: list[Citation] = []
        self.used_backend = "unresolved"

    def _cite(self, doc_id: str, locator: str, summary: str) -> str:
        cid = str(uuid.uuid4())
        self.citations.append(
            Citation(
                citation_id=cid, source_type=SourceType.VECTOR_POLICY, evidence_kind=EvidenceKind.POLICY_GUIDANCE,
                source_id=doc_id, source_locator=locator, retrieved_at=datetime.now(timezone.utc),
                region=self.region, summary=summary,
            )
        )
        return cid

    def search(self, query_text: str, limit: int = 5) -> list[PolicyPassage]:
        native = getattr(self.repo, "search_policy_passages_native", None)
        if self.vector_backend == "hana" and callable(native):
            try:
                rows = native(query_text, limit=limit)
                self.used_backend = "hana_native_vector"
                return [
                    PolicyPassage(
                        document_id=r["DOCUMENT_ID"], passage_locator=r["PASSAGE_LOCATOR"], text=r["TEXT"],
                        similarity_score=float(r.get("SIMILARITY", 0.0)),
                        citation_id=self._cite(r["DOCUMENT_ID"], r["PASSAGE_LOCATOR"], f"Policy passage {r['DOCUMENT_ID']}/{r['PASSAGE_LOCATOR']}"),
                    )
                    for r in rows
                ]
            except Exception:
                pass  # fall through to local ranking rather than fail the whole CaseFile

        self.used_backend = "local_tfidf_fallback"
        return self._local_rank(query_text, limit)

    def _local_rank(self, query_text: str, limit: int) -> list[PolicyPassage]:
        passages = self.repo.list_policy_passages()
        if not passages:
            return []
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        texts = [p["TEXT"] for p in passages]
        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform(texts + [query_text])
        sims = cosine_similarity(matrix[-1], matrix[:-1])[0]
        ranked = sorted(zip(passages, sims), key=lambda x: -x[1])[:limit]
        return [
            PolicyPassage(
                document_id=p["DOCUMENT_ID"], passage_locator=p["PASSAGE_LOCATOR"], text=p["TEXT"],
                similarity_score=round(float(score), 4),
                citation_id=self._cite(p["DOCUMENT_ID"], p["PASSAGE_LOCATOR"], f"Policy passage {p['DOCUMENT_ID']}/{p['PASSAGE_LOCATOR']}"),
            )
            for p, score in ranked if score > 0
        ]
