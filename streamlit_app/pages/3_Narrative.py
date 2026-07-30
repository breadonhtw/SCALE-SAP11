"""Narrative & explanation: cited generation with validation flags (B2).

Editing + attestation arrive in B3; this page generates, displays, and
persists versions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components import api_client, ui  # noqa: E402

st.set_page_config(page_title="Narrative", page_icon="🛡️", layout="wide")

KIND_TAG = {
    "exact_fact": ":green-badge[exact fact]",
    "relationship_inference": ":blue-badge[relationship path]",
    "policy_guidance": ":violet-badge[policy guidance]",
    "historical_reference": ":gray-badge[historical reference]",
    "ai_synthesis": ":orange-badge[AI synthesis]",
}

case_id = st.session_state.get("case_id") or st.query_params.get("case_id")
if not case_id:
    st.info("Assemble a case from an alert first.")
    st.page_link("app.py", label="← Back to queue")
    st.stop()

st.page_link("pages/2_Case_File.py", label="← Case file")
st.title(f"Narrative & explanation — {case_id}")


def render_generation(payload: dict, heading: str) -> None:
    gen = payload["generation"]
    val = payload["validation"]
    st.subheader(heading)
    backend_note = ("SAP AI Core orchestration" if gen["backend"] == "sap_ai_core"
                    else "deterministic fallback (no model call)")
    st.caption(f"Backend **{backend_note}** · model `{gen['model_name']}` · "
               f"prompt `{gen['prompt_version']}` · id {gen['generation_id']}"
               + (f" · {gen['usage']['total_tokens']} tokens"
                  if gen.get("usage") else ""))
    c1, c2, c3 = st.columns(3)
    c1.metric("Citation coverage", f"{val['citation_coverage']:.0%}")
    c2.metric("Unsupported sentences", val["unsupported_sentences"])
    c3.metric("Numeric mismatches", val["numeric_mismatches"])
    for s in payload["sentences"]:
        tag = KIND_TAG.get(s["kind"], s["kind"])
        cits = " ".join(f"`{c}`" for c in s["citation_ids"]) or "*(no citation)*"
        flag = "" if s["supported"] else " ⚠️ **unsupported — verify manually**"
        st.markdown(f"{tag} {s['text']} — {cits}{flag}")


with st.container(border=True):
    st.warning(ui.DRAFT_LABEL, icon="✍️")

col_a, col_b = st.columns(2)
with col_a:
    question = st.text_input("Question for the explanation",
                             "Why is this alert prioritised?")
    if st.button("Generate explanation", type="primary"):
        with st.spinner("Generating cited explanation…"):
            st.session_state["explanation"] = api_client.explain(case_id, question)
with col_b:
    if st.button("Generate narrative draft", type="primary"):
        with st.spinner("Generating supporting narrative…"):
            api_client.generate_draft(case_id)

if "explanation" in st.session_state \
        and st.session_state["explanation"].get("case_id") == case_id:
    render_generation(st.session_state["explanation"], "Explanation")

try:
    draft = api_client.latest_draft(case_id)
except api_client.ApiError as exc:
    draft = None
    if exc.code != "DRAFT_NOT_FOUND":
        raise

if draft:
    st.divider()
    st.subheader(f"Draft v{draft['draft_version']} — {draft['draft_id']}")
    st.caption(f"Created by **{draft['created_by_type']}** at "
               f"{draft['created_at']} UTC · status {draft['verification_status']}")
    if draft.get("generation"):
        render_generation(draft, "Draft (cited sentences)")
    else:
        st.write(draft["content"])

st.divider()
with st.expander("Audit history"):
    events = api_client.audit_events(case_id)["items"]
    if events:
        ui.table([{"When (UTC)": e["occurred_at"], "Event": e["event_type"],
                   "Actor": f"{e['actor_type']}:{e['actor_id']}",
                   "Object": f"{e['object_type']}:{e['object_id']}",
                   "Correlation": e["correlation_id"]}
                  for e in events])
    else:
        st.caption("No audit events yet.")
