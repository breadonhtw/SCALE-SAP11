"""Investigation Assistant chat (B4).

Custom agent on SAP AI Core orchestration tool-calling; the tools are the
same backend endpoints the rest of the cockpit uses. Conversation state is
UI-only; everything material the agent does (case files, drafts, workflows)
persists via the backend (shared-state rule, CLAUDE.md §6).
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "src"))
from components import api_client, ui  # noqa: E402
from trustsphere.assistant import AssistantLoop  # noqa: E402

st.set_page_config(page_title="Investigation Assistant", page_icon="🛡️",
                   layout="wide")
ui.page_chrome()

st.title("Investigation Assistant")
st.caption("**TrustSphere Financial Crime Investigation Agent — custom agent "
           "on SAP AI Core orchestration; production surface: Joule Studio.** "
           "The agent explains, assembles, and drafts; it cannot dismiss, "
           "file, block, or decide — the human investigator does, with "
           "attestation.")

try:
    health = api_client.health()
except Exception:
    st.error("Backend unreachable. Start it with: "
             "`uvicorn trustsphere.api.app:app --port 8000`")
    st.stop()
ui.backend_banner(health)


@st.cache_resource
def _loop() -> AssistantLoop:
    return AssistantLoop(base_url=api_client.BASE_URL)


if "assistant_api_messages" not in st.session_state:
    st.session_state["assistant_api_messages"] = []
    st.session_state["assistant_display"] = []

# First-run onboarding (Google PAIR formula; MS first-run: capabilities +
# output-quality expectations). Shown only while the conversation is empty.
if not st.session_state["assistant_display"]:
    with st.container(border=True):
        st.markdown(
            "**What this agent does:** explains why an alert is prioritised, "
            "assembles the evidence case file, drafts cited narratives, and "
            "routes cases to human review.\n\n"
            "**What it can't do:** dismiss, close, file, block, or decide — "
            "those actions don't exist in its tools; they happen on the "
            "Review & Decide page, by you, with attestation.\n\n"
            "**What to expect:** every factual answer is grounded in tool "
            "results with citation ids you can verify; it does not learn "
            "from your actions.\n\n"
            "**You help by** asking about specific alerts or cases and "
            "verifying citations before relying on a draft.")

alert_id = st.session_state.get("alert_id")
suggestion = None
cols = st.columns((3, 2))
if alert_id:
    cols[0].caption(f"Context: alert `{alert_id}` "
                    f"(case `{st.session_state.get('case_id', '—')}`)")
    if cols[1].button(f"Ask: why is {alert_id} critical?"):
        suggestion = f"Why is alert {alert_id} prioritised? Explain the evidence."

for item in st.session_state["assistant_display"]:
    with st.chat_message(item["role"]):
        st.markdown(item["text"])
        for ev in item.get("tool_events", []):
            with st.expander(f"🔧 {ev['name']}({', '.join(f'{k}={v}' for k, v in ev['arguments'].items())})"):
                st.code(ev["result_summary"], language="json")
        if item.get("meta"):
            st.caption(item["meta"])

prompt = st.chat_input("Ask about an alert, evidence, or next steps…")
prompt = prompt or suggestion

if prompt:
    st.session_state["assistant_display"].append(
        {"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        # Neutral process language (MS human-centered agents: "process/
        # analyze", never "think/reason/understand").
        with st.spinner("Running tools against the case APIs "
                        "(live orchestration calls)…"):
            turn = _loop().run(st.session_state["assistant_api_messages"],
                               prompt)
        st.markdown(turn.text)
        for ev in turn.tool_events:
            with st.expander(f"🔧 {ev.name}({', '.join(f'{k}={v}' for k, v in ev.arguments.items())})"):
                st.code(ev.result_summary, language="json")
        meta = (f"model `{turn.model_name}` · {turn.total_tokens} tokens · "
                f"{len(turn.tool_events)} tool call(s) · prompt assistant-1.0")
        st.caption(meta)
    st.session_state["assistant_api_messages"] = turn.messages
    st.session_state["assistant_display"].append({
        "role": "assistant", "text": turn.text,
        "tool_events": [{"name": e.name, "arguments": e.arguments,
                          "result_summary": e.result_summary}
                         for e in turn.tool_events],
        "meta": meta,
    })

if st.button("Clear conversation"):
    st.session_state["assistant_api_messages"] = []
    st.session_state["assistant_display"] = []
    st.rerun()
