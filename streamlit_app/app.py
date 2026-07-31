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
ui.page_chrome()

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

PAGE_SIZE = 50
page = int(st.session_state.get("queue_page", 0))
data = api_client.queue(limit=PAGE_SIZE, offset=page * PAGE_SIZE)
total = data.get("total", data["count"])
if total == 0:
    st.info("No scored alerts yet — the queue ranks alerts that have been "
            "through the deterministic scoring engine.")
    if st.button("Score all alerts now", type="primary"):
        with st.spinner("Running deterministic urgency scoring on all alerts…"):
            result = api_client.score_all()
        st.success(f"Scored {result['scored_count']} alerts.")
        st.rerun()
    st.stop()

pages = (total + PAGE_SIZE - 1) // PAGE_SIZE

# Hero KPI strip (team-level only — no individual metrics). Non-zero-only
# rendering per NN/g salience-by-removal; oldest breach from rank #1, which
# the queue policy guarantees is the longest-breached alert.
rank1 = api_client.queue(limit=1, offset=0)["queue"][0] if page else \
    (data["queue"][0] if data["queue"] else None)
decided_on_page = sum(1 for r in data["queue"] if r.get("CASE_STATUS"))
tiles = st.columns(3)
tiles[0].metric("Open alerts", f"{total:,}")
if rank1:
    tiles[1].metric("Oldest SLA breach", ui.sla_text(rank1.get("SLA_DUE_AT"))
                    .replace("BREACHED ", "").replace(" ago", ""))
if decided_on_page:
    tiles[2].metric("Decided on this page", decided_on_page)

st.subheader(f"Ranked alert queue — {total:,} open alerts")
nav_l, nav_mid, nav_r = st.columns((1, 4, 1))
if nav_l.button("← Prev", disabled=page == 0):
    st.session_state["queue_page"] = page - 1
    st.rerun()
nav_mid.caption(
    f"Showing ranks {page * PAGE_SIZE + 1:,}–{page * PAGE_SIZE + data['count']:,} "
    f"of {total:,} (page {page + 1}/{pages}). Queue order is server-side policy "
    "(hard overrides → urgency tier → SLA remaining → score; complexity only "
    f"as tie-break). Backend `{data['backend']}`")
if nav_r.button("Next →", disabled=page >= pages - 1):
    st.session_state["queue_page"] = page + 1
    st.rerun()

header = st.columns((1, 6, 3, 2, 4, 3, 2))
for col, name in zip(header, ("#", "Alert", "Tier", "Score", "SLA",
                              "Complexity", "")):
    col.markdown(f"**{name}**")

DECIDED_STATUSES = {"ESCALATED": "escalated", "RETURNED_FOR_EDIT": "returned",
                    "INFO_REQUESTED": "info requested"}

for rank, row in enumerate(data["queue"], start=page * PAGE_SIZE + 1):
    cols = st.columns((1, 6, 3, 2, 4, 3, 2))
    cols[0].write(rank)
    # Status encodings: colour + text + shape (IBM Carbon: never colour alone)
    override = " :red-badge[🔴 override]" if row.get("HARD_OVERRIDE_CODE") else ""
    decided = DECIDED_STATUSES.get(row.get("CASE_STATUS") or "")
    chip = f" :green-badge[✓ {decided}]" if decided else ""
    cols[1].markdown(f"`{row['ALERT_ID']}`{override}{chip}  \n"
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
