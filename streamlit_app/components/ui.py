"""Shared UI helpers: badges, SLA countdown, mandated labels, money formatting."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

TIER_BADGE = {
    "CRITICAL": ":red-badge[CRITICAL]",
    "HIGH": ":orange-badge[HIGH]",
    "MEDIUM": ":blue-badge[MEDIUM]",
    "LOW": ":gray-badge[LOW]",
}

# Required labels (CLAUDE.md §12 / §10) — use verbatim, do not paraphrase.
ADVISORY_LABEL = "Advisory — pilot/shadow mode. Not part of the regulatory urgency score."
DRAFT_LABEL = "AI-generated draft — investigator verification required. Not approved for filing."


def tier_badge(tier: str) -> str:
    return TIER_BADGE.get(tier, tier)


def sla_text(due_at_iso: str) -> str:
    due = datetime.fromisoformat(due_at_iso.replace("Z", "+00:00"))
    hours = (due - datetime.now(timezone.utc)).total_seconds() / 3600
    if hours < 0:
        return f"BREACHED {abs(hours):.0f}h ago"
    if hours < 48:
        return f"{hours:.0f}h remaining"
    return f"{hours / 24:.1f}d remaining"


def backend_banner(health: dict) -> None:
    if health.get("backend") != "hana":
        st.warning(
            f"Backend: **{health.get('backend', 'unreachable')}** — local prototype "
            "fallback. Not connected to SAP HANA Cloud.", icon="⚠️")


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


def money(amount: str, currency: str) -> str:
    # Amounts arrive as exact decimal strings; format for display only.
    whole, _, frac = amount.partition(".")
    return f"{currency} {int(whole):,}.{frac or '00'}"
