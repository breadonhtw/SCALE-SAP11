"""Generator implementation on the SAP AI Core orchestration v2 API."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from .aicore import AICoreClient, AICoreError
from .base import GenerationResult, Sentence, Task

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "config" / "prompts"
PROMPT_VERSIONS: dict[Task, str] = {"explain": "explain-1.1",
                                    "narrative": "narrative-1.1"}

SENTENCES_SCHEMA = {
    "name": "cited_output",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "sentences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "citation_ids": {"type": "array",
                                          "items": {"type": "string"}},
                        "kind": {"type": "string",
                                 "enum": ["exact_fact", "relationship_inference",
                                          "policy_guidance", "historical_reference",
                                          "ai_synthesis"]},
                    },
                    "required": ["text", "citation_ids", "kind"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["sentences"],
        "additionalProperties": False,
    },
}


def _load_prompt(task: Task) -> str:
    return (PROMPTS_DIR / f"{PROMPT_VERSIONS[task]}.md").read_text(encoding="utf-8")


# Content-filtering module mirrored from the team's portal-authored
# configuration (sap/orchestration/trustsphere-narrative-config.json,
# exported from AI Launchpad Generative AI -> Orchestration and test-run
# against fincrime content 2026-07-30). Categories that collide with
# legitimate financial-crime evidence (non_violent_crimes,
# specialized_advice) are deliberately not enabled.
_LLAMA_GUARD_CATEGORIES = {
    "child_exploitation": True, "code_interpreter_abuse": True,
    "defamation": True, "elections": True, "hate": True,
    "indiscriminate_weapons": True, "intellectual_property": True,
    "privacy": True, "self_harm": True, "sex_crimes": True,
    "sexual_content": True, "violent_crimes": True,
}
FILTERING_MODULE = {
    "input": {"filters": [
        {"type": "azure_content_safety", "config": {}},
        {"type": "llama_guard_3_8b", "config": _LLAMA_GUARD_CATEGORIES},
    ]},
    "output": {"filters": [
        {"type": "azure_content_safety", "config": {}},
        {"type": "llama_guard_3_8b", "config": _LLAMA_GUARD_CATEGORIES},
    ]},
}


def _parse_sentences(text: str) -> list[Sentence]:
    # Models occasionally wrap JSON in fences despite instructions.
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    data = json.loads(cleaned)
    return [Sentence(text=s["text"], citation_ids=list(s.get("citation_ids", [])),
                     kind=s.get("kind", "ai_synthesis"))
            for s in data["sentences"]]


class OrchestrationGenerator:
    """Cited generation through the orchestration deployment (harmonized API)."""

    def __init__(self, model_name: str | None = None, client: AICoreClient | None = None):
        # Default gpt-4.1-mini: selected by the team via AI Launchpad
        # (Generative AI -> Orchestration) and live-verified 2026-07-30
        # (docs/capability-matrix.md). Any other model must pass
        # scripts/verify_orchestration.py first.
        self.model_name = (model_name
                           or os.environ.get("TRUSTSPHERE_GEN_MODEL", "gpt-4.1-mini"))
        self.client = client or AICoreClient()

    def _config(self, task: Task, use_json_schema: bool) -> dict:
        template = [
            {"role": "system", "content": _load_prompt(task)},
            {"role": "user",
             "content": "Question: {{?question}}\n\nCaseFile JSON:\n{{?case_file_json}}"},
        ]
        prompt: dict = {"template": template,
                        "defaults": {"question": "Summarise the key evidence."}}
        if use_json_schema:
            prompt["response_format"] = {"type": "json_schema",
                                          "json_schema": SENTENCES_SCHEMA}
        return {
            "modules": {
                "prompt_templating": {
                    "prompt": prompt,
                    "model": {
                        "name": self.model_name,
                        "version": "latest",
                        # 3000: real CaseFiles carry ~25 transactions; 1200
                        # truncated the narrative JSON mid-string (live find).
                        "params": {"max_completion_tokens": 3000, "temperature": 0.1},
                    },
                },
                "filtering": FILTERING_MODULE,
            }
        }

    @staticmethod
    def _saved_config_ref(task: Task) -> dict | None:
        """Saved orchestration configuration in AI Core, if configured.

        Env: TRUSTSPHERE_ORCH_CONFIG_ID_EXPLAIN / _NARRATIVE — the configuration
        IDs from AI Launchpad (Generative AI -> Orchestration). When set, the
        prompt/model recipe is governed in the tenant instead of sent inline.
        """
        config_id = os.environ.get(f"TRUSTSPHERE_ORCH_CONFIG_ID_{task.upper()}")
        return {"id": config_id} if config_id else None

    def generate(self, task: Task, case_file: dict,
                 question: str | None = None) -> GenerationResult:
        placeholders = {"case_file_json": json.dumps(case_file, ensure_ascii=False)}
        if question:
            placeholders["question"] = question
        config_ref = self._saved_config_ref(task)
        resp = None
        if config_ref:
            try:
                resp = self.client.v2_completion({}, placeholders,
                                                 config_ref=config_ref)
                _parse_sentences(resp["final_result"]["choices"][0]["message"]["content"])
            except (AICoreError, json.JSONDecodeError, KeyError):
                # Saved config unusable (bad ref, or output not parseable JSON
                # because saved configs lack our strict json_schema response
                # format) -> fall through to the inline recipe.
                resp = None
        if resp is None:
            try:
                resp = self.client.v2_completion(self._config(task, True), placeholders)
            except AICoreError as exc:
                # Some providers reject response_format; downgrade to prompt-only JSON.
                if exc.status == 400 and "response_format" in str(exc):
                    resp = self.client.v2_completion(self._config(task, False),
                                                     placeholders)
                else:
                    raise
        final = resp["final_result"]
        message = final["choices"][0]["message"]["content"]
        return GenerationResult(
            task=task,
            sentences=_parse_sentences(message),
            backend="sap_ai_core",
            model_name=final.get("model", self.model_name),
            model_version=final.get("model", "latest"),
            prompt_version=PROMPT_VERSIONS[task],
            generation_id=f"GEN-{uuid.uuid4().hex[:8]}",
            usage=final.get("usage"),
            request_id=resp.get("request_id"),
        )
