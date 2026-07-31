"""Agent loop over SAP AI Core orchestration v2 tool calling.

Client-side loop (docs/sap-reference.md §2.5): send template(system)+tools,
read `tool_calls`, execute against the backend, feed results back as
role:"tool" messages via messages_history, repeat until the model answers in
text or the iteration cap is reached. `tool_choice` is not supported by the
orchestration contract — steering is prompt-only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from trustsphere.generation.aicore import AICoreClient
from trustsphere.assistant.tools import TOOL_DEFS, execute_tool

PROMPT_PATH = (Path(__file__).resolve().parents[3]
               / "config" / "prompts" / "assistant-1.0.md")
PROMPT_VERSION = "assistant-1.0"
MAX_ITERATIONS = 8
MAX_TOOL_RESULT_CHARS = 8000


@dataclass
class ToolEvent:
    name: str
    arguments: dict
    result_summary: str


@dataclass
class AssistantTurn:
    text: str
    tool_events: list[ToolEvent] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)  # updated api history
    total_tokens: int = 0
    model_name: str = ""


class AssistantLoop:
    def __init__(self, client: AICoreClient | None = None,
                 model_name: str | None = None,
                 base_url: str | None = None):
        self.client = client or AICoreClient()
        self.model_name = (model_name
                           or os.environ.get("TRUSTSPHERE_GEN_MODEL",
                                             "gpt-4.1-mini"))
        self.base_url = base_url or os.environ.get(
            "TRUSTSPHERE_API_BASE_URL", "http://127.0.0.1:8000")

    def _config(self, with_tools: bool) -> dict:
        prompt: dict = {
            "template": [{"role": "system",
                          "content": PROMPT_PATH.read_text(encoding="utf-8")}],
            "defaults": {},
        }
        if with_tools:
            prompt["tools"] = TOOL_DEFS
        return {"modules": {"prompt_templating": {
            "prompt": prompt,
            "model": {"name": self.model_name, "version": "latest",
                       "params": {"max_completion_tokens": 900,
                                   "temperature": 0.1}},
        }}}

    def run(self, history: list[dict], user_message: str) -> AssistantTurn:
        messages = list(history) + [{"role": "user", "content": user_message}]
        events: list[ToolEvent] = []
        total_tokens = 0
        model_seen = self.model_name

        for iteration in range(MAX_ITERATIONS + 1):
            with_tools = iteration < MAX_ITERATIONS
            resp = self.client.v2_completion(self._config(with_tools), {},
                                             messages_history=messages)
            final = resp["final_result"]
            model_seen = final.get("model", model_seen)
            total_tokens += (final.get("usage") or {}).get("total_tokens", 0)
            message = final["choices"][0]["message"]
            tool_calls = message.get("tool_calls")

            if not tool_calls:
                text = message.get("content") or ""
                messages.append({"role": "assistant", "content": text})
                return AssistantTurn(text=text, tool_events=events,
                                     messages=messages,
                                     total_tokens=total_tokens,
                                     model_name=model_seen)

            messages.append({"role": "assistant",
                             "content": message.get("content") or "",
                             "tool_calls": tool_calls})
            for tc in tool_calls:
                name = tc["function"]["name"]
                raw_args = tc["function"].get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    result = {"error": "Tool arguments were not valid JSON; "
                                        "re-emit the call with valid JSON."}
                    args = {"_raw": raw_args}
                else:
                    result = execute_tool(name, args, self.base_url)
                content = json.dumps(result, ensure_ascii=False, default=str)
                if len(content) > MAX_TOOL_RESULT_CHARS:
                    content = content[:MAX_TOOL_RESULT_CHARS] + "…(truncated)"
                events.append(ToolEvent(
                    name=name, arguments=args,
                    result_summary=content[:400]))
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": content})

        # Defensive: cap loop exited without a final text (shouldn't happen —
        # the last iteration runs without tools).
        return AssistantTurn(text="Tool-call limit reached for this question "
                                   "— please narrow it down.",
                             tool_events=events, messages=messages,
                             total_tokens=total_tokens, model_name=model_seen)
