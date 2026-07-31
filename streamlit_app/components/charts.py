"""Pixel-styled charts — plain SVG, zero data-analysis dependencies.

Streamlit's native st.bar_chart/st.line_chart pull in pandas/altair
internally. Pandas is blocked on the dev machine (Application Control
policy — see components/ui.py `table()`), so charts here are hand-built SVG
instead. That also means they match the pixel-art SAP theme exactly rather
than looking like a bolted-on library widget.
"""

from __future__ import annotations

import html

import streamlit as st

from . import theme

_FONT = "font-family:'VT323',monospace"


def hbar_chart(items: list[tuple[str, float]], color: str = "blue",
               unit: str = "", row_height: int = 32) -> None:
    """Horizontal chunky bar chart. `items`: [(label, value), ...]."""
    if not items:
        st.caption("No data.")
        return
    fg = theme.CHIP_COLORS.get(color, theme.SAP_BLUE)
    clean = [(html.escape(str(label)), float(val or 0)) for label, val in items]
    max_val = max((v for _, v in clean), default=0) or 1
    label_w, chart_w, right_pad = 168, 340, 60
    total_w = label_w + chart_w + right_pad
    total_h = row_height * len(clean)
    rows = []
    for i, (label, val) in enumerate(clean):
        y = i * row_height
        bar_w = max(3, (val / max_val) * chart_w)
        rows.append(
            f'<text x="0" y="{y + row_height / 2}" dominant-baseline="middle" '
            f'style="{_FONT};font-size:16px;fill:#F5F7FA">{label}</text>'
            f'<rect x="{label_w}" y="{y + 5}" width="{bar_w:.1f}" '
            f'height="{row_height - 12}" fill="{fg}" '
            f'stroke="{theme.SAP_NAVY}" stroke-width="2"/>'
            f'<text x="{label_w + bar_w + 8}" y="{y + row_height / 2}" '
            f'dominant-baseline="middle" style="{_FONT};font-size:16px;'
            f'fill:#F5F7FA">{val:,.0f}{unit}</text>'
        )
    svg = (f'<svg width="100%" viewBox="0 0 {total_w} {total_h}" '
           f'xmlns="http://www.w3.org/2000/svg">' + "".join(rows) + "</svg>")
    st.markdown(svg, unsafe_allow_html=True)


def progress_bar(pct: float, label: str = "", color: str = "blue") -> None:
    """Blocky 'health bar' style progress indicator — segmented squares,
    not a smooth gradient fill, to stay on-theme."""
    fg = theme.CHIP_COLORS.get(color, theme.SAP_BLUE)
    pct = float(pct or 0)
    segments, filled = 20, round(max(0.0, min(1.0, pct)) * 20)
    cells = "".join(
        f'<rect x="{i * 17}" y="0" width="14" height="20" '
        f'fill="{fg if i < filled else "transparent"}" '
        f'stroke="{theme.SAP_NAVY_LIGHT}" stroke-width="2"/>'
        for i in range(segments)
    )
    caption = f'{label} {pct:.0%}' if label else f'{pct:.0%}'
    st.markdown(
        f'<div style="{_FONT};font-size:15px;color:#F5F7FA;margin-bottom:4px">'
        f'{caption}</div>'
        f'<svg width="100%" viewBox="0 0 {segments * 17} 20" '
        f'xmlns="http://www.w3.org/2000/svg">{cells}</svg>',
        unsafe_allow_html=True)
