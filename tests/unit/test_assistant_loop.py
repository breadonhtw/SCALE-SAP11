"""Assistant loop + guardrail tests (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from trustsphere.assistant.loop import MAX_ITERATIONS, AssistantLoop  # noqa: E402
from trustsphere.assistant.tools import TOOL_DEFS, TOOL_NAMES, execute_tool  # noqa: E402


def _resp(message: dict, tokens: int = 10) -> dict:
    return {"request_id": "r", "final_result": {
        "model": "test-model", "usage": {"total_tokens": tokens},
        "choices": [{"message": message, "finish_reason": "stop"}]}}


class FakeClient:
    """Scripted v2_completion; records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def v2_completion(self, config, placeholder_values,
                      messages_history=None, config_ref=None):
        self.calls.append({"config": config,
                           "messages": list(messages_history or [])})
        return self.responses.pop(0)


def _loop(client) -> AssistantLoop:
    return AssistantLoop(client=client, model_name="test-model",
                         base_url="http://unit-test-never-called")


def test_tool_call_then_answer(monkeypatch):
    executed = []

    def fake_execute(name, args, base_url):
        executed.append((name, args))
        return {"ok": True, "alert": "ALERT-9001"}

    monkeypatch.setattr("trustsphere.assistant.loop.execute_tool",
                        fake_execute)
    client = FakeClient([
        _resp({"role": "assistant", "content": None, "tool_calls": [
            {"id": "tc-1", "type": "function", "function": {
                "name": "get_alert_details",
                "arguments": json.dumps({"alert_id": "ALERT-9001"})}}]}),
        _resp({"role": "assistant", "content": "It is critical because…"}),
    ])
    turn = _loop(client).run([], "Why is ALERT-9001 critical?")

    assert executed == [("get_alert_details", {"alert_id": "ALERT-9001"})]
    assert turn.text == "It is critical because…"
    assert len(turn.tool_events) == 1
    # second call's history must contain the assistant tool_call + tool result
    second_msgs = client.calls[1]["messages"]
    assert second_msgs[-1]["role"] == "tool"
    assert second_msgs[-1]["tool_call_id"] == "tc-1"
    assert second_msgs[-2]["tool_calls"][0]["id"] == "tc-1"
    # conversation history returned for the next turn ends with final answer
    assert turn.messages[-1] == {"role": "assistant",
                                 "content": "It is critical because…"}
    assert turn.total_tokens == 20


def test_malformed_arguments_fed_back_as_error(monkeypatch):
    monkeypatch.setattr("trustsphere.assistant.loop.execute_tool",
                        lambda *a, **k: {"should": "not run"})
    client = FakeClient([
        _resp({"role": "assistant", "content": None, "tool_calls": [
            {"id": "tc-1", "type": "function", "function": {
                "name": "get_alert_details", "arguments": "{not json"}}]}),
        _resp({"role": "assistant", "content": "Please clarify."}),
    ])
    turn = _loop(client).run([], "hi")
    tool_msg = client.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert "not valid JSON" in tool_msg["content"]
    assert turn.text == "Please clarify."


def test_iteration_cap_forces_toolless_final_call(monkeypatch):
    monkeypatch.setattr("trustsphere.assistant.loop.execute_tool",
                        lambda *a, **k: {"ok": True})
    tool_call_resp = _resp({"role": "assistant", "content": None,
                            "tool_calls": [{"id": "tc", "type": "function",
                                             "function": {"name": "get_alert_details",
                                                           "arguments": "{}"}}]})
    client = FakeClient([tool_call_resp] * MAX_ITERATIONS
                        + [_resp({"role": "assistant", "content": "done"})])
    turn = _loop(client).run([], "loop forever")
    assert turn.text == "done"
    assert len(client.calls) == MAX_ITERATIONS + 1
    # the final call must NOT offer tools
    last_prompt = client.calls[-1]["config"]["modules"]["prompt_templating"]["prompt"]
    assert "tools" not in last_prompt
    for call in client.calls[:-1]:
        assert "tools" in call["config"]["modules"]["prompt_templating"]["prompt"]


def test_registry_is_bounded():
    assert TOOL_NAMES == {"get_alert_details", "calculate_regulatory_urgency",
                          "assemble_case_file", "draft_supporting_narrative",
                          "start_human_review"}
    forbidden = ("dismiss", "close_alert", "file_sar", "block", "approve",
                 "decide", "delete", "update_source")
    for tool in TOOL_DEFS:
        name = tool["function"]["name"]
        assert not any(word in name for word in forbidden)
        assert len(name) <= 64


def test_unknown_tool_returns_error_not_exception():
    result = execute_tool("dismiss_alert", {"alert_id": "X"},
                          base_url="http://unit-test-never-called")
    assert "error" in result
    assert "Unknown tool" in result["error"]
