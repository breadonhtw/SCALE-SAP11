"""TrustSphere RiskOps Copilot — investigator cockpit (ranked queue).

Run:  streamlit run streamlit_app/app.py
Needs the backend (mock or real) at TRUSTSPHERE_API_BASE_URL.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from components import api_client, ui  # noqa: E402

st.set_page_config(page_title="TrustSphere RiskOps Copilot",
                   page_icon="🛡️", layout="wide")

st.title("TrustSphere RiskOps Copilot")
st.caption("Rules determine regulatory urgency. Predictive AI forecasts operational "
           "risk. HybridRAG establishes context. Generative AI explains and drafts. "
           "**Humans decide.**")

try:
    health = api_client.health()
except Exception:
    st.error("Backend unreachable. Start it with: "
             "`uvicorn mock_api.app:app --port 8000`")
    st.stop()

ui.backend_banner(health)

data = api_client.list_alerts(status="open")
st.subheader(f"Ranked alert queue — {data['total']} open alerts")
st.caption("Queue order is server-side policy (hard overrides → urgency tier → "
           f"SLA remaining → score). Scoring policy `{data['policy_version']}` · "
           f"as of {data['as_of']} UTC")

header = st.columns((1, 5, 6, 3, 2, 4, 3, 3, 2))
for col, name in zip(header, ("#", "Alert", "Customer", "Tier", "Score",
                              "SLA", "Complexity", "Advisory", "")):
    col.markdown(f"**{name}**")

for item in data["items"]:
    cols = st.columns((1, 5, 6, 3, 2, 4, 3, 3, 2))
    urgency = item["urgency"]
    cols[0].write(item["queue_rank"])
    override = " 🔴" if urgency["hard_override_code"] else ""
    cols[1].write(f"`{item['alert_id']}`{override}  \n"
                  f"{item['alert_type']} · {item['status']}")
    company = item["company"]
    kyc = " · **KYC EXPIRED**" if company["kyc_effective_status"] == "EXPIRED" else ""
    cols[2].markdown(f"{company['legal_name']}  \n"
                     f"{company['jurisdiction_code']} · "
                     f"KYC risk {company['kyc_risk_rating']}{kyc}")
    cols[3].markdown(ui.tier_badge(urgency["tier"]))
    cols[4].write(f"{urgency['score']:.1f}")
    cols[5].write(ui.sla_text(item["sla"]["due_at"]))
    cols[6].write(item["complexity"]["band"])
    advisory = item.get("advisory")
    cols[7].write(advisory["band"] if advisory else "—")
    if cols[8].button("Open", key=f"open-{item['alert_id']}"):
        st.session_state["alert_id"] = item["alert_id"]
        st.switch_page("pages/1_Alert_Detail.py")

st.caption("🔴 = hard override active (tier set by policy override; factor "
           "breakdown preserved). Advisory column is operational shadow-mode "
           "output, never part of the urgency score.")
