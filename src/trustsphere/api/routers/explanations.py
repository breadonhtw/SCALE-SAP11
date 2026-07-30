"""Track B generation endpoint (see docs/api-contract.md "Ownership boundary").

POST /cases/{case_id}/explanations runs cited generation over the persisted
CaseFile via SAP AI Core orchestration (or the deterministic fallback),
validates citations/numbers, and — for narrative tasks — persists the result
through the same draft path as POST /cases/{id}/drafts.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from trustsphere.api.deps import get_repo
from trustsphere.api.errors import AppError, NotFoundError
from trustsphere.domain.decisions import ActorType, AuditEventType
from trustsphere.generation import get_generator
from trustsphere.generation.fallback import FallbackGenerator
from trustsphere.generation.validation import validate
from trustsphere.persistence.base import Repository
from trustsphere.services.audit import record_event

router = APIRouter(tags=["generation"])


class ExplanationRequest(BaseModel):
    question: str | None = None
    task: Literal["explain", "narrative"] = "explain"
    # None -> persist iff task == "narrative"
    persist_draft: bool | None = None


def _draft_content(result) -> str:
    lines = []
    for s in result.sentences:
        cits = f" [{', '.join(s.citation_ids)}]" if s.citation_ids else ""
        lines.append(f"{s.text}{cits}")
    return "\n\n".join(lines)


@router.post("/cases/{case_id}/explanations")
def generate_explanation(
    case_id: str,
    body: ExplanationRequest = ExplanationRequest(),
    repo: Repository = Depends(get_repo),
):
    if repo.get_case(case_id) is None:
        raise NotFoundError(f"case_id {case_id!r} not found")
    case_file = repo.get_latest_case_file(case_id)
    if case_file is None:
        raise AppError(
            "CASE_FILE_REQUIRED",
            "Assemble the case file before requesting generation "
            "(POST /cases/{id}/assemble).",
            status_code=422,
        )
    cf_dict = case_file.model_dump(mode="json")

    generator = get_generator()
    try:
        result = generator.generate(body.task, cf_dict, body.question)
    except Exception:
        # AI Core unreachable mid-request -> honest deterministic fallback
        # (CLAUDE.md §18); backend label in the payload tells the truth.
        result = FallbackGenerator().generate(body.task, cf_dict, body.question)
    validation = validate(result, cf_dict)

    record_event(
        repo, case_id=case_id, event_type=AuditEventType.EXPLANATION_GENERATED,
        actor_type=ActorType.AGENT, actor_id=result.generation_id,
        object_type="EXPLANATION", object_id=result.generation_id,
        details={"task": body.task, "generation_backend": result.backend,
                 "model": result.model_name,
                 "citation_coverage": validation["citation_coverage"],
                 "numeric_mismatches": validation["numeric_mismatches"]},
    )

    persist = body.persist_draft if body.persist_draft is not None \
        else body.task == "narrative"
    draft = None
    if persist:
        draft = repo.save_draft(
            case_id=case_id, content=_draft_content(result),
            generation_id=result.generation_id,
            prompt_version=result.prompt_version,
            model_version=result.model_name,
            created_by_type="agent", verification_status="unverified",
        )
        record_event(
            repo, case_id=case_id, event_type=AuditEventType.DRAFT_CREATED,
            actor_type=ActorType.AGENT, actor_id=result.generation_id,
            object_type="NARRATIVE_DRAFT", object_id=draft["DRAFT_ID"],
            details={"draft_version": draft["DRAFT_VERSION"],
                     "generation_backend": result.backend},
        )

    return {
        "backend": repo.backend_label(),
        "explanation": {
            "task": result.task,
            "content": result.content,
            "sentences": [{"text": s.text, "citation_ids": s.citation_ids,
                           "kind": s.kind, "supported": s.supported}
                          for s in result.sentences],
            "generation": {"generation_id": result.generation_id,
                            "generation_backend": result.backend,
                            "model_name": result.model_name,
                            "prompt_version": result.prompt_version,
                            "usage": result.usage,
                            "request_id": result.request_id},
            "validation": validation,
        },
        "draft": draft,
    }
