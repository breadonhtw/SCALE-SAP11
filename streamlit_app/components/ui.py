"""Shared UI helpers: badges, SLA countdown, mandated labels, money formatting."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st

from . import theme

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

TIER_COLOR = {"CRITICAL": "red", "HIGH": "gold", "MEDIUM": "blue", "LOW": "gray"}

# Required labels (CLAUDE.md §12 / §10) — use verbatim, do not paraphrase.
ADVISORY_LABEL = "Advisory — pilot/shadow mode. Not part of the regulatory urgency score."
DRAFT_LABEL = "AI-generated draft — investigator verification required. Not approved for filing."

# Material Symbols only — no emoji anywhere in the cockpit (rendered by
# Streamlit's built-in icon font, not pictures, so they stay legible and
# on-brand across themes).
ICON_OVERRIDE = ":material/report:"
ICON_WARNING = ":material/warning:"
ICON_BLOCKED = ":material/block:"
ICON_ADVISORY = ":material/insights:"
ICON_DRAFT = ":material/edit_note:"
ICON_BACK = ":material/arrow_back:"
ICON_FORWARD = ":material/arrow_forward:"
ICON_TOOL = ":material/build:"

# Five-step investigation flow — a consistent progress indicator so an
# investigator always knows where they are in the pipeline and what's next.
FLOW_STEPS = ["Queue", "Alert detail", "Case file", "Narrative", "Review & decide"]


def stepper(current_index: int) -> None:
    """Render the flow position as chips — colour + text, never colour or an
    icon alone (see CLAUDE.md UX note). Plain HTML, not a markdown badge
    directive — those don't render on every installed Streamlit version
    (see theme.chip docstring)."""
    segments = [
        theme.chip(f"{i + 1}. {name}", "blue" if i == current_index else "gray")
        for i, name in enumerate(FLOW_STEPS)
    ]
    st.markdown("&nbsp;→&nbsp;".join(segments), unsafe_allow_html=True)


def tier_badge(tier: str) -> str:
    """Returns raw HTML — render with `unsafe_allow_html=True`."""
    return theme.chip(tier, TIER_COLOR.get(tier, "gray"))


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


def sla_breach_days(due_at_iso: str | None) -> float | None:
    """Days past SLA due date, or None if not breached / no due date."""
    if not due_at_iso:
        return None
    due = _parse_ts(due_at_iso)
    if due is None:
        return None
    hours = (datetime.now(timezone.utc) - due).total_seconds() / 3600
    return hours / 24 if hours > 0 else None


def backend_banner(health: dict) -> None:
    """Governance requires the backend always be disclosed honestly
    (CLAUDE.md §26) — it does not require a full-width alert box every
    page. A live HANA connection is a single muted caption; a fallback
    still surfaces as a real warning, since that's the case worth
    interrupting someone for."""
    backend = health.get("backend", "unreachable")
    if str(backend).startswith("hana"):
        st.caption(f":material/cloud_done: Backend `{backend}` — live SAP HANA Cloud")
    else:
        st.warning(
            f"Backend: **{backend}** — local prototype fallback. "
            "Not connected to SAP HANA Cloud.", icon=ICON_WARNING)


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
