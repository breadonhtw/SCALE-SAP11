"""TrustSphere RiskOps Copilot — investigator cockpit (ranked queue).

Run:  streamlit run streamlit_app/app.py
Backend (Track A API) at TRUSTSPHERE_API_BASE_URL.
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
             "`uvicorn trustsphere.api.app:app --port 8000`")
    st.stop()

ui.backend_banner(health)

data = api_client.queue()
if data["count"] == 0:
    st.info("No scored alerts yet — the queue ranks alerts that have been "
            "through the deterministic scoring engine.")
    if st.button("Score all alerts now", type="primary"):
        with st.spinner("Running deterministic urgency scoring on all alerts…"):
            result = api_client.score_all()
        st.success(f"Scored {result['scored_count']} alerts.")
        st.rerun()
    st.stop()

st.subheader(f"Ranked alert queue — {data['count']} alerts")
st.caption("Queue order is server-side policy (hard overrides → urgency tier → "
           "SLA remaining → score; complexity only as tie-break). "
           f"Backend `{data['backend']}`")

header = st.columns((1, 6, 3, 2, 4, 3, 2))
for col, name in zip(header, ("#", "Alert", "Tier", "Score", "SLA",
                              "Complexity", "")):
    col.markdown(f"**{name}**")

for rank, row in enumerate(data["queue"], start=1):
    cols = st.columns((1, 6, 3, 2, 4, 3, 2))
    cols[0].write(rank)
    override = " 🔴" if row.get("HARD_OVERRIDE_CODE") else ""
    cols[1].write(f"`{row['ALERT_ID']}`{override}  \n"
                  f"{row.get('ALERT_TYPE', '?')} · {row.get('STATUS', '?')}")
    cols[2].markdown(ui.tier_badge(row.get("URGENCY_TIER", "?")))
    cols[3].write(f"{row.get('URGENCY_SCORE', 0):.1f}")
    cols[4].write(ui.sla_text(row.get("SLA_DUE_AT")))
    cols[5].write(f"{row.get('COMPLEXITY_BAND', '?')} "
                  f"({row.get('COMPLEXITY_POINTS', '?')})")
    if cols[6].button("Open", key=f"open-{row['ALERT_ID']}"):
        st.session_state["alert_id"] = row["ALERT_ID"]
        st.switch_page("pages/1_Alert_Detail.py")

st.caption("🔴 = hard override active (tier set by policy override; factor "
           "breakdown preserved).")
