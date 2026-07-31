"""Shared UI helpers: badges, SLA countdown, mandated labels, money formatting."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st

# Storage/API/audit stay UTC; only rendering converts (CLAUDE.md §7/§23).
DISPLAY_TZ = ZoneInfo("Asia/Singapore")


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def fmt_ts(value) -> str:
    """Render a UTC timestamp in the display timezone: '31 Jul 2026, 07:14 SGT'."""
    dt = _parse_ts(value)
    if dt is None:
        return "—" if not value else str(value)
    return dt.astimezone(DISPLAY_TZ).strftime("%d %b %Y, %H:%M SGT")

TIER_BADGE = {
    "CRITICAL": ":red-badge[CRITICAL]",
    "HIGH": ":orange-badge[HIGH]",
    "MEDIUM": ":blue-badge[MEDIUM]",
    "LOW": ":gray-badge[LOW]",
}

# Required labels (CLAUDE.md §12 / §10) — use verbatim, do not paraphrase.
ADVISORY_LABEL = "Advisory — pilot/shadow mode. Not part of the regulatory urgency score."
DRAFT_LABEL = "AI-generated draft — investigator verification required. Not approved for filing."

# Design tokens from the team's Claude Design mockup (TrustSphere Cockpit.dc.html)
_CHROME_CSS = """
<style>
@font-face{font-family:'72';src:url('https://cdn.jsdelivr.net/npm/@sap-theming/theming-base-content/content/Base/baseLib/baseTheme/fonts/72-Regular.woff2') format('woff2');font-weight:400;font-display:swap;}
@font-face{font-family:'72';src:url('https://cdn.jsdelivr.net/npm/@sap-theming/theming-base-content/content/Base/baseLib/baseTheme/fonts/72-Semibold.woff2') format('woff2');font-weight:600;font-display:swap;}
@font-face{font-family:'72';src:url('https://cdn.jsdelivr.net/npm/@sap-theming/theming-base-content/content/Base/baseLib/baseTheme/fonts/72-Bold.woff2') format('woff2');font-weight:700;font-display:swap;}
html, body, [data-testid="stAppViewContainer"] *{font-family:'72',-apple-system,'Segoe UI',sans-serif;}
code, pre, kbd{font-family:'IBM Plex Mono',ui-monospace,monospace !important;}
[data-testid="stSidebar"]{background:#0C1116;border-right:1px solid #1F2933;}
[data-testid="stMetric"]{background:#161D25;border:1px solid #1F2933;border-radius:10px;padding:14px 16px;}
[data-testid="stMetricLabel"]{color:#93A1B0;text-transform:uppercase;letter-spacing:.06em;font-size:.72rem;}
h1, h2, h3{letter-spacing:-.01em;}
</style>
"""

_SIDEBAR_BRAND = """
<div style="padding:4px 2px 10px 2px;">
  <div style="font-weight:700;font-size:1.05rem;letter-spacing:.12em;color:#E6ECF2;">TRUSTSPHERE</div>
  <div style="color:#4DB1FF;font-size:.85rem;font-weight:600;">RiskOps Copilot</div>
  <div style="color:#5C6B7A;font-size:.72rem;margin-top:2px;">SAP BTP · Singapore (APAC)</div>
</div>
<div style="border-top:1px solid #1F2933;margin:6px 0 10px 0;"></div>
<div style="font-size:.68rem;letter-spacing:.08em;color:#93A1B0;line-height:1.9;">
  <span style="color:#6FB4E8;">RULES</span> →
  <span style="color:#A88FE0;">PREDICTIVE</span> →
  <span style="color:#63C591;">RETRIEVAL</span> →
  <span style="color:#E8A268;">GENERATIVE</span> →
  <span style="color:#E6ECF2;font-weight:600;">HUMANS&nbsp;DECIDE</span>
</div>
"""


def page_chrome() -> None:
    """Shared design-skin chrome (fonts, tiles, sidebar brand). Call once per
    page, right after st.set_page_config."""
    st.markdown(_CHROME_CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown(_SIDEBAR_BRAND, unsafe_allow_html=True)


def tier_badge(tier: str) -> str:
    return TIER_BADGE.get(tier, tier)


def _humanise_hours(hours: float) -> str:
    days = hours / 24
    if hours < 48:
        return f"{hours:.0f}h"
    if days < 90:
        return f"{days:.0f}d"
    return f"{days / 365.25:.1f}y"


def sla_text(due_at_iso: str | None) -> str:
    if not due_at_iso:
        return "—"
    due = datetime.fromisoformat(str(due_at_iso).replace("Z", "+00:00"))
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    hours = (due - datetime.now(timezone.utc)).total_seconds() / 3600
    if hours < 0:
        return f"BREACHED {_humanise_hours(abs(hours))} ago"
    return f"{_humanise_hours(hours)} remaining"


def backend_banner(health: dict) -> None:
    backend = health.get("backend", "unreachable")
    if str(backend).startswith("hana"):
        st.caption(f"🟢 Backend: **{backend}** — live SAP HANA Cloud")
    else:
        st.warning(
            f"Backend: **{backend}** — local prototype fallback. "
            "Not connected to SAP HANA Cloud.", icon="⚠️")


def table(rows: list[dict]) -> None:
    # Markdown table instead of st.dataframe: pandas' compiled DLLs are blocked
    # by the Application Control policy on the dev machine, and st.dataframe
    # imports pandas unconditionally.
    if not rows:
        return
    headers = list(rows[0].keys())
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(esc(r.get(h, "")) for h in headers) + " |"
              for r in rows]
    st.markdown("\n".join(lines))


def audit_table(events: list[dict]) -> None:
    if not events:
        st.caption("No audit events yet.")
        return
    table([{"When (SGT)": fmt_ts(e["occurred_at"]), "Event": e["event_type"],
            "Actor": f"{e['actor_type']}:{e['actor_id']}",
            "Object": f"{e['object_type']}:{e['object_id']}",
            "Correlation": e["correlation_id"]}
           for e in events])


def money(amount: str, currency: str) -> str:
    # Amounts arrive as exact decimal strings; format for display only.
    whole, _, frac = amount.partition(".")
    return f"{currency} {int(whole):,}.{frac or '00'}"
