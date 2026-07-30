"""Narrative & explanation: cited generation with validation flags (B2).

Editing + attestation arrive in B3; this page generates, displays, and
persists versions through the Track A backend.
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


def render_generation(explanation: dict, heading: str) -> None:
    gen = explanation["generation"]
    val = explanation["validation"]
    st.subheader(heading)
    backend_note = ("SAP AI Core orchestration"
                    if gen["generation_backend"] == "sap_ai_core"
                    else "deterministic fallback (no model call)")
    usage = gen.get("usage") or {}
    st.caption(f"Generation backend **{backend_note}** · model "
               f"`{gen['model_name']}` · prompt `{gen['prompt_version']}` · "
               f"id {gen['generation_id']}"
               + (f" · {usage['total_tokens']} tokens" if usage else ""))
    c1, c2, c3 = st.columns(3)
    c1.metric("Citation coverage", f"{val['citation_coverage']:.0%}")
    c2.metric("Unsupported sentences", val["unsupported_sentences"])
    c3.metric("Numeric mismatches", val["numeric_mismatches"])
    for s in explanation["sentences"]:
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
        with st.spinner("Generating cited explanation (live model call)…"):
            resp = api_client.explain(case_id, question)
        st.session_state["explanation"] = {"case_id": case_id,
                                            "payload": resp["explanation"]}
with col_b:
    st.write("")
    st.write("")
    if st.button("Generate narrative draft", type="primary"):
        with st.spinner("Generating supporting narrative (live model call)…"):
            resp = api_client.generate_narrative(case_id)
        st.session_state["narrative"] = {"case_id": case_id,
                                          "payload": resp["explanation"]}

exp = st.session_state.get("explanation")
if exp and exp["case_id"] == case_id:
    render_generation(exp["payload"], "Explanation")

nar = st.session_state.get("narrative")
if nar and nar["case_id"] == case_id:
    st.divider()
    render_generation(nar["payload"], "Narrative (cited sentences)")

try:
    draft = api_client.latest_draft(case_id)["draft"]
except api_client.ApiError as exc:
    draft = None
    if exc.status != 404:
        raise

if draft:
    st.divider()
    st.subheader(f"Persisted draft v{draft['DRAFT_VERSION']} — {draft['DRAFT_ID']}")
    st.caption(f"Created by **{draft['CREATED_BY_TYPE']}** · model "
               f"`{draft.get('MODEL_VERSION', '—')}` · prompt "
               f"`{draft.get('PROMPT_VERSION', '—')}` · status "
               f"{draft.get('VERIFICATION_STATUS', 'unverified')} · "
               f"{ui.fmt_ts(draft.get('CREATED_AT'))}")
    st.text(draft["CONTENT"])

st.divider()
if st.button("Review & decide →", type="primary"):
    st.switch_page("pages/4_Review_Decide.py")

with st.expander("Audit history"):
    ui.audit_table(api_client.audit_events(case_id)["audit_events"])
