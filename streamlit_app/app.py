"""TrustSphere RiskOps Copilot — investigator cockpit (ranked queue).

Run:  streamlit run streamlit_app/app.py
Backend (Track A API) at TRUSTSPHERE_API_BASE_URL.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from components import api_client, charts, theme, ui  # noqa: E402

st.set_page_config(page_title="TrustSphere RiskOps Copilot", layout="wide")
theme.inject()

st.title("TrustSphere RiskOps Copilot")
st.caption("Rules determine regulatory urgency. Predictive AI forecasts operational "
           "risk. HybridRAG establishes context. Generative AI explains and drafts. "
           "**Humans decide.**")
ui.stepper(0)

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


@st.cache_data(ttl=30)
def _full_queue(n: int) -> list[dict]:
    """One extra fetch of the whole scored queue, cached briefly, purely
    for the aggregate charts below — the paginated table above still drives
    its own request so opening a page never waits on this."""
    return api_client.queue(limit=n, offset=0)["queue"]


full_queue = _full_queue(total)

# Hero KPI strip (team-level only — no individual metrics). Non-zero-only
# rendering per NN/g salience-by-removal; oldest breach from rank #1, which
# the queue policy guarantees is the longest-breached alert.
rank1 = full_queue[0] if full_queue else None
decided_on_page = sum(1 for r in data["queue"] if r.get("CASE_STATUS"))
tiles = st.columns(3)
tiles[0].metric("Open alerts", f"{total:,}")
if rank1:
    tiles[1].metric("Oldest SLA breach", ui.sla_text(rank1.get("SLA_DUE_AT"))
                    .replace("BREACHED ", "").replace(" ago", ""))
if decided_on_page:
    tiles[2].metric("Decided on this page", decided_on_page)

TIER_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
# ComplexityBand values (domain/alerts.py) — there is no CRITICAL band
COMPLEXITY_ORDER = ("VERY_HIGH", "HIGH", "MEDIUM", "LOW")
tier_counts = {t: 0 for t in TIER_ORDER}
complexity_counts = {c: 0 for c in COMPLEXITY_ORDER}
type_counts: dict[str, int] = {}
age_buckets = {"< 30d": 0, "30–90d": 0, "90d–1y": 0, "1–2y": 0, "2y+": 0}
for r in full_queue:
    t = r.get("URGENCY_TIER")
    if t in tier_counts:
        tier_counts[t] += 1
    c = r.get("COMPLEXITY_BAND")
    if c in complexity_counts:
        complexity_counts[c] += 1
    a_type = r.get("ALERT_TYPE") or "UNKNOWN"
    type_counts[a_type] = type_counts.get(a_type, 0) + 1
    days = ui.sla_breach_days(r.get("SLA_DUE_AT"))
    if days is None:
        continue
    elif days < 30:
        age_buckets["< 30d"] += 1
    elif days < 90:
        age_buckets["30–90d"] += 1
    elif days < 365:
        age_buckets["90d–1y"] += 1
    elif days < 730:
        age_buckets["1–2y"] += 1
    else:
        age_buckets["2y+"] += 1
type_counts_sorted = sorted(type_counts.items(), key=lambda kv: -kv[1])[:8]


theme.section_header("Queue at a glance")
row1_l, row1_r = st.columns(2)
with row1_l:
    theme.section_header("By tier", "blue")
    charts.hbar_chart(list(tier_counts.items()), color="blue")
with row1_r:
    theme.section_header("By alert type", "violet")
    charts.hbar_chart(type_counts_sorted, color="violet")
row2_l, row2_r = st.columns(2)
with row2_l:
    theme.section_header("By complexity", "green")
    charts.hbar_chart(list(complexity_counts.items()), color="green")
with row2_r:
    theme.section_header("Backlog age", "gold", "Time past each alert's SLA due date")
    charts.hbar_chart(list(age_buckets.items()), color="gold")

theme.section_header(f"Ranked alert queue — {total:,} open alerts",
                     help_text="Order: hard overrides → urgency tier → "
                               "SLA remaining → score. Complexity is a "
                               "tie-break only.")
nav_l, nav_mid, nav_r = st.columns((1, 4, 1))
if nav_l.button("← Prev", disabled=page == 0):
    st.session_state["queue_page"] = page - 1
    st.rerun()
nav_mid.caption(
    f"Showing ranks {page * PAGE_SIZE + 1:,}–{page * PAGE_SIZE + data['count']:,} "
    f"of {total:,} (page {page + 1}/{pages})")
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
    # Status encodings: colour + explicit text (IBM Carbon: never colour alone)
    override = (" " + theme.chip("Override", "red")
                if row.get("HARD_OVERRIDE_CODE") else "")
    decided = DECIDED_STATUSES.get(row.get("CASE_STATUS") or "")
    status_chip = f" {theme.chip(decided.capitalize(), 'green')}" if decided else ""
    cols[1].markdown(f"`{row['ALERT_ID']}`{override}{status_chip}  \n"
                     f"{row.get('ALERT_TYPE', '?')} · {row.get('STATUS', '?')}",
                     unsafe_allow_html=True)
    cols[2].markdown(ui.tier_badge(row.get("URGENCY_TIER", "?")), unsafe_allow_html=True)
    cols[3].write(f"{row.get('URGENCY_SCORE', 0):.1f}")
    cols[4].write(ui.sla_text(row.get("SLA_DUE_AT")))
    cols[5].markdown(f"{row.get('COMPLEXITY_BAND', '?')}",
                     help=f"{row.get('COMPLEXITY_POINTS', '?')} points — full "
                          "breakdown on the alert page")
    if cols[6].button("Open", key=f"open-{row['ALERT_ID']}"):
        st.session_state["alert_id"] = row["ALERT_ID"]
        st.switch_page("pages/1_Alert_Detail.py")
