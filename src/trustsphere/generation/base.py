"""Generator contract shared by the AI Core implementation and the fallback.

The result shape is stable across backends (CLAUDE.md §18: fallbacks preserve
API contracts), so endpoints and UI never care which backend produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

Task = Literal["explain", "narrative"]

# Evidence kinds shown distinctly in the UI (CLAUDE.md §11 citation contract).
KINDS = ("exact_fact", "relationship_inference", "policy_guidance",
         "historical_reference", "ai_synthesis")


@dataclass
class Sentence:
    text: str
    citation_ids: list[str] = field(default_factory=list)
    kind: str = "ai_synthesis"
    supported: bool = False  # set by validation, not by the model


@dataclass
class GenerationResult:
    task: Task
    sentences: list[Sentence]
    backend: Literal["sap_ai_core", "fallback"]
    model_name: str
    model_version: str
    prompt_version: str
    generation_id: str
    usage: dict | None = None  # {prompt_tokens, completion_tokens, total_tokens}
    request_id: str | None = None

    @property
    def content(self) -> str:
        return " ".join(s.text for s in self.sentences)


class Generator(Protocol):
    def generate(self, task: Task, case_file: dict,
                 question: str | None = None) -> GenerationResult: ...
