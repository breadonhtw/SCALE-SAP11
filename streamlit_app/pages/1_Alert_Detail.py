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

alert = api_client.alert_detail(alert_id)
urgency = alert["urgency"]

st.page_link("app.py", label="← Back to queue")
st.title(f"Alert {alert['alert_id']}")
st.markdown(f"{ui.tier_badge(urgency['tier'])} &nbsp; **{alert['alert_type']}** · "
            f"{alert['status']} · source priority {alert['alert_priority']} · "
            f"region {alert['region']}")

if urgency["hard_override_code"]:
    st.error(f"**Hard override: {urgency['hard_override_code']}** — "
             f"{urgency['hard_override_reason']}  \n"
             "The override sets the tier; the factor breakdown below is preserved.",
             icon="🔴")

left, right = st.columns((3, 2))

with left:
    st.subheader(f"Regulatory urgency {urgency['score']:.1f} / 100")
    st.caption(f"Deterministic, versioned policy `{urgency['policy_version']}` · "
               f"calculated {urgency['calculated_at']} UTC")
    ui.table(
        [{"Factor": f["label"], "Raw value": str(f["raw_value"]),
          "Normalised": f["normalised_value"], "Weight": f["weight"],
          "Points": f["weighted_points"], "Reason code": f"`{f['reason_code']}`"}
         for f in alert["factors"]])

    rule = alert["rule"]
    st.markdown(f"**Triggering rule** `{rule['rule_id']}` — {rule['rule_name']}")
    st.caption(f"Rule intent: {rule['rule_intent']}")

with right:
    st.subheader("SLA")
    sla = alert["sla"]
    st.metric("Time remaining", ui.sla_text(sla["due_at"]),
              help=f"Due {sla['due_at']} UTC")

    st.subheader("Operational advisory")
    advisory = alert.get("advisory")
    if advisory:
        st.info(ui.ADVISORY_LABEL, icon="🔬")
        st.metric("Predicted resolution",
                  f"{advisory['prediction_value']:.0f}h",
                  delta=f"{advisory['sla_margin_hours']:+.0f}h vs SLA",
                  delta_color="normal")
        st.caption(f"Band **{advisory['band']}** · model "
                   f"`{advisory['model_name']} {advisory['model_version']}` · "
                   f"scored {advisory['scored_at']} UTC")
    else:
        st.caption("No advisory prediction for this alert.")

    st.subheader("Complexity (operational, not urgency)")
    comp = alert["complexity"]
    st.write(f"**{comp['band']}** (score {comp['score']})")
    st.caption(" · ".join(comp["drivers"]))

    company = alert["company"]
    st.subheader("Customer")
    st.write(f"**{company['legal_name']}** (`{company['company_id']}`)")
    st.write(f"{company['registration_number']} · {company['lei_code']}")
    st.write(f"KYC risk {company['kyc_risk_rating']} · "
             f"{company['jurisdiction_code']} · "
             f"KYC **{company['kyc_effective_status']}**")

st.divider()
if st.button("Assemble case file", type="primary"):
    case = api_client.get_or_create_case(alert_id)
    with st.spinner("Assembling typed CaseFile from deterministic sources…"):
        api_client.assemble_case(case["case_id"])
    st.session_state["case_id"] = case["case_id"]
    st.switch_page("pages/2_Case_File.py")

if alert.get("related_alert_ids"):
    st.caption("Related alerts: " +
               ", ".join(f"`{a}`" for a in alert["related_alert_ids"]))
