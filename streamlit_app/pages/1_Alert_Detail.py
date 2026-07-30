"""Alert detail: urgency breakdown, hard override, complexity, advisory SLA."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components import api_client, ui  # noqa: E402

st.set_page_config(page_title="Alert Detail", page_icon="🛡️", layout="wide")

alert_id = st.session_state.get("alert_id") or st.query_params.get("alert_id")
if not alert_id:
    st.info("Open an alert from the queue first.")
    st.page_link("app.py", label="← Back to queue")
    st.stop()

detail = api_client.alert_detail(alert_id)
alert = detail["alert"]
score = detail["priority_score"]
if score is None:
    score = api_client.score_alert(alert_id)["score"]
prediction = detail["predictive_sla"]

st.page_link("app.py", label="← Back to queue")
st.title(f"Alert {alert['alert_id']}")
st.markdown(f"{ui.tier_badge(score['urgency_tier'])} &nbsp; "
            f"**{alert.get('alert_type', '?')}** · {alert.get('status', '?')} · "
            f"source priority {alert.get('alert_priority', '?')} · "
            f"backend `{detail['backend']}`")

override = score.get("hard_override")
if override:
    st.error(f"**Hard override: {override['code']}** (forces tier "
             f"{override['forced_tier']}) — {override['reason']}  \n"
             "The override sets the tier; the factor breakdown below is preserved.",
             icon="🔴")

left, right = st.columns((3, 2))

with left:
    st.subheader(f"Regulatory urgency {score['urgency_score']:.1f} / 100")
    st.caption(f"Deterministic, versioned policy `{score['policy_version']}` · "
               f"calculated {ui.fmt_ts(score['calculated_at'])}")
    ui.table(
        [{"Factor": f["factor_code"].replace("_", " ").title(),
          "Raw value": str(f.get("raw_value")),
          "Normalised (0-100)": f["normalised_value"],
          "Weight": f["weight"],
          "Points": f["weighted_points"],
          "Reason code": f"`{f['reason_code']}`"}
         for f in score["factors"]])
    if score.get("caveats"):
        st.caption("Caveats: " + " · ".join(score["caveats"]))

with right:
    st.subheader("SLA")
    st.metric("Time remaining", ui.sla_text(alert.get("sla_due_at")),
              help=f"Due {ui.fmt_ts(alert.get('sla_due_at'))}")

    st.subheader("Operational advisory")
    if prediction is None:
        if st.button("Run advisory SLA prediction"):
            prediction = api_client.predict_sla(alert_id)["prediction"]
            st.rerun()
        st.caption("Not yet predicted for this alert.")
    if prediction:
        pred = {k.lower(): v for k, v in prediction.items()}
        st.info(pred.get("label") or ui.ADVISORY_LABEL, icon="🔬")
        st.metric(pred.get("prediction_type", "prediction"),
                  f"{float(pred.get('prediction_value', 0)):.1f}h")
        st.caption(f"Model `{pred.get('model_name')} "
                   f"{pred.get('model_version')}` · advisory only — never part "
                   "of the urgency score")

    st.subheader("Complexity (operational, not urgency)")
    st.write(f"**{score['complexity_band']}** "
             f"(points {score['complexity_points']})")

    st.subheader("Customer")
    st.write(f"Company `{alert.get('company_id') or '—'}` · "
             f"transaction `{alert.get('transaction_id') or '—'}`")
    st.caption("Full customer profile appears in the assembled case file.")

st.divider()
if st.button("Assemble case file", type="primary"):
    case = api_client.create_case(alert_id)["case"]
    with st.spinner("Assembling typed CaseFile (SQL + graph + vector retrieval)…"):
        api_client.assemble_case(case["CASE_ID"])
    st.session_state["case_id"] = case["CASE_ID"]
    st.switch_page("pages/2_Case_File.py")
