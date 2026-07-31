"""Narrative & explanation: cited generation with validation flags (B2).

Editing + attestation arrive in B3; this page generates, displays, and
persists versions through the Track A backend.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components import api_client, charts, theme, ui  # noqa: E402

st.set_page_config(page_title="Narrative", layout="wide")
theme.inject()

KIND_TAG = {
    "exact_fact": theme.chip("exact fact", "green"),
    "relationship_inference": theme.chip("relationship path", "blue"),
    "policy_guidance": theme.chip("policy guidance", "violet"),
    "historical_reference": theme.chip("historical reference", "gray"),
    "ai_synthesis": theme.chip("AI synthesis", "gold"),
}

case_id = st.session_state.get("case_id") or st.query_params.get("case_id")
if not case_id:
    st.info("Assemble a case from an alert first.")
    st.page_link("app.py", label="← Back to queue")
    st.stop()

st.page_link("pages/2_Case_File.py", label="Case file", icon=ui.ICON_BACK)
st.title(f"Narrative & explanation — {case_id}")
ui.stepper(3)


def render_generation(explanation: dict, heading: str) -> None:
    gen = explanation["generation"]
    val = explanation["validation"]
    st.subheader(heading)
    backend_note = ("SAP AI Core orchestration"
                    if gen["generation_backend"] == "sap_ai_core"
                    else "deterministic fallback (no model call)")
    usage = gen.get("usage") or {}
    with st.expander(f"Generation details — {backend_note}"):
        st.caption(f"Model `{gen['model_name']}` · prompt `{gen['prompt_version']}` · "
                   f"id {gen['generation_id']}"
                   + (f" · {usage['total_tokens']} tokens" if usage else ""))
    c1, c2, c3 = st.columns((2, 1, 1))
    coverage = val["citation_coverage"]
    cov_color = "green" if coverage >= 0.9 else "gold" if coverage >= 0.7 else "red"
    with c1:
        charts.progress_bar(coverage, "Citation coverage", color=cov_color)
    c2.metric("Unsupported sentences", val["unsupported_sentences"])
    c3.metric("Numeric mismatches", val["numeric_mismatches"])
    for s in explanation["sentences"]:
        tag = KIND_TAG.get(s["kind"], s["kind"])
        cits = " ".join(f"`{c}`" for c in s["citation_ids"]) or "*(no citation)*"
        flag = "" if s["supported"] else " " + theme.chip("Unsupported — verify manually", "red")
        st.markdown(f"{tag} {s['text']} — {cits}{flag}", unsafe_allow_html=True)


with st.container(border=True):
    st.warning(ui.DRAFT_LABEL, icon=ui.ICON_DRAFT)

def _run_generation(kind: str, question: str | None = None) -> None:
    """Latency as a described process, not a generic spinner (Apple HIG:
    say what the system is doing), completing with the actual outcome."""
    with st.status(f"Sending case evidence to SAP AI Core orchestration "
                   f"(gpt-4.1-mini, content filtering active)…",
                   expanded=False) as status:
        if kind == "explanation":
            resp = api_client.explain(case_id, question)
        else:
            resp = api_client.generate_narrative(case_id)
        payload = resp["explanation"]
        val = payload["validation"]
        status.update(
            label=(f"Generated and validated — citation coverage "
                   f"{val['citation_coverage']:.0%}, "
                   f"{val['numeric_mismatches']} numeric mismatches"),
            state="complete")
    st.session_state[kind] = {"case_id": case_id, "payload": payload}


col_a, col_b = st.columns(2)
with col_a:
    question = st.text_input("Question for the explanation",
                             "Why is this alert prioritised?")
    if st.button("Generate explanation", type="primary"):
        _run_generation("explanation", question)
with col_b:
    st.write("")
    st.write("")
    if st.button("Generate narrative draft", type="primary"):
        _run_generation("narrative")

exp = st.session_state.get("explanation")
if exp and exp["case_id"] == case_id:
    render_generation(exp["payload"], "Explanation")
    if st.button("↻ Regenerate explanation",
                 help="Runs the same cited-generation call again; "
                      "nothing is overwritten"):
        _run_generation("explanation", question)
        st.rerun()

nar = st.session_state.get("narrative")
if nar and nar["case_id"] == case_id:
    st.divider()
    render_generation(nar["payload"], "Narrative (cited sentences)")
    if st.button("↻ Regenerate narrative",
                 help="Produces a fresh draft as a new version; earlier "
                      "versions are retained"):
        _run_generation("narrative")
        st.rerun()

try:
    draft = api_client.latest_draft(case_id)["draft"]
except api_client.ApiError as exc:
    draft = None
    if exc.status != 404:
        raise

if draft:
    st.divider()
    st.subheader(f"Persisted draft v{draft['DRAFT_VERSION']}")
    st.caption(f"Created by **{draft['CREATED_BY_TYPE']}** · "
               f"{ui.fmt_ts(draft.get('CREATED_AT'))} · status "
               f"{draft.get('VERIFICATION_STATUS', 'unverified')}")
    st.text(draft["CONTENT"])
    with st.expander("Generation details"):
        st.caption(f"Draft `{draft['DRAFT_ID']}` · model "
                   f"`{draft.get('MODEL_VERSION', '—')}` · prompt "
                   f"`{draft.get('PROMPT_VERSION', '—')}`")

st.divider()
if st.button("Review & decide →", type="primary"):
    st.switch_page("pages/4_Review_Decide.py")

with st.expander("Audit history"):
    ui.audit_table(api_client.audit_events(case_id)["audit_events"])
