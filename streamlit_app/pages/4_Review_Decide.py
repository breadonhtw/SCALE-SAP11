"""Review & decide: draft editing, attestation, human decision, audit (B3).

The only place a decision can be made — and it requires explicit attestation,
enforced server-side (422 ATTESTATION_REQUIRED). CLAUDE.md §14.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components import api_client, ui  # noqa: E402

st.set_page_config(page_title="Review & Decide", page_icon="🛡️", layout="wide")

DECISION_LABELS = {
    "Approve for escalation": "approve_for_escalation",
    "Return for edit": "return_for_edit",
    "Request information": "request_information",
}
DECISION_TO_WORKFLOW_STATUS = {
    "approve_for_escalation": "APPROVED",
    "return_for_edit": "RETURNED",
    "request_information": "INFO_REQUESTED",
}

case_id = st.session_state.get("case_id") or st.query_params.get("case_id")
if not case_id:
    st.info("Assemble a case from an alert first.")
    st.page_link("app.py", label="← Back to queue")
    st.stop()

state = api_client.get_case(case_id)
case = state["case"]
workflow = state["workflow"]
decisions = state["decisions"]

st.page_link("pages/2_Case_File.py", label="← Case file")
st.page_link("pages/3_Narrative.py", label="← Narrative")
st.title(f"Review & decide — {case_id}")
st.caption(f"Case status **{case['STATUS']}** · team {case.get('ASSIGNED_TEAM', '—')} · "
           f"region {case.get('REGION', '—')} · backend `{state['backend']}`")

# -- 1. draft review & edit -------------------------------------------------

st.header("1 · Review the narrative")
try:
    draft = api_client.latest_draft(case_id)["draft"]
except api_client.ApiError as exc:
    if exc.status != 404:
        raise
    draft = None

if draft is None:
    st.warning("No narrative draft yet — generate one on the Narrative page first.")
    st.page_link("pages/3_Narrative.py", label="Go to Narrative →")
else:
    st.warning(ui.DRAFT_LABEL, icon="✍️")
    st.caption(f"Latest: **v{draft['DRAFT_VERSION']}** by "
               f"**{draft['CREATED_BY_TYPE']}** · model "
               f"`{draft.get('MODEL_VERSION', '—')}` · prompt "
               f"`{draft.get('PROMPT_VERSION', '—')}` · "
               f"{ui.fmt_ts(draft.get('CREATED_AT'))}")
    edited = st.text_area("Investigator revision", value=draft["CONTENT"],
                          height=320, label_visibility="collapsed")
    if st.button("Save investigator revision"):
        if edited.strip() and edited != draft["CONTENT"]:
            saved = api_client.save_draft_edit(case_id, edited)["draft"]
            st.success(f"Saved as v{saved['DRAFT_VERSION']} (created_by human).")
            st.rerun()
        else:
            st.info("No changes to save.")

# -- 2. human review workflow -----------------------------------------------

st.header("2 · Human review workflow")
if workflow is None:
    if st.button("Start human review", type="primary",
                 disabled=draft is None):
        wf = api_client.start_review_workflow(
            case_id, draft_id=draft["DRAFT_ID"] if draft else None)["workflow"]
        st.rerun()
    if draft is None:
        st.caption("A draft is required before review can start.")
else:
    st.write(f"Workflow `{workflow['workflow_id']}` · status "
             f"**{workflow['status']}** · started {ui.fmt_ts(workflow['started_at'])}"
             + (f" · completed {ui.fmt_ts(workflow['completed_at'])}"
                if workflow.get("completed_at") else ""))
    if workflow.get("is_fallback"):
        st.warning("Local review-state machine (fallback) — not a live "
                   "SAP Build Process Automation instance. Honest label per "
                   "CLAUDE.md §18.", icon="⚠️")
    else:
        st.success(f"Live SAP Build Process Automation instance "
                   f"`{workflow.get('external_instance_id')}` — the approval "
                   "task appears in the reviewer's My Inbox (SAP Build lobby).")
        if st.button("Sync status from SAP Build"):
            synced = api_client.sync_workflow(case_id)
            st.caption(f"SBPA instance status: {synced.get('sbpa_status')}")
            st.rerun()

# -- 3. decision --------------------------------------------------------------

st.header("3 · Decision (human only)")
if decisions:
    st.caption("Decision history:")
    ui.table([{"When (SGT)": ui.fmt_ts(d["decided_at"]), "Decision": d["decision_type"],
               "By": d["decided_by"], "Attested": d["attested"],
               "Rationale": d["rationale"]} for d in decisions])

with st.form("decision-form"):
    label = st.radio("Decision", list(DECISION_LABELS.keys()), horizontal=True)
    rationale = st.text_area(
        "Rationale (required — recorded verbatim in the audit trail)")
    decided_by = st.text_input("Decided by", value="investigator.demo")
    attested = st.checkbox(
        "I have reviewed the evidence and this narrative; this decision is "
        "mine and I attest to it.")
    submitted = st.form_submit_button("Record decision", type="primary")

if submitted:
    decision_type = DECISION_LABELS[label]
    try:
        resp = api_client.record_decision(case_id, decision_type,
                                          rationale, decided_by, attested)
    except api_client.ApiError as exc:
        if exc.code == "ATTESTATION_REQUIRED":
            st.error("Refused by the backend: **attestation is required** "
                     "before a decision is recorded. Tick the attestation "
                     "checkbox — a human must own this decision "
                     "(CLAUDE.md §14).", icon="🚫")
        elif exc.status == 422:
            st.error(f"Refused: {exc}")
        else:
            raise
    else:
        st.success(f"Decision `{resp['decision']['decision_id']}` recorded.")
        if workflow is not None and workflow["status"] not in (
                "APPROVED", "RETURNED"):
            api_client.transition_workflow(
                case_id, DECISION_TO_WORKFLOW_STATUS[decision_type])
        st.rerun()

# -- 4. audit trail -----------------------------------------------------------

st.header("4 · Audit trail (append-only)")
ui.audit_table(api_client.audit_events(case_id)["audit_events"])
