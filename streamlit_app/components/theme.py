"""SAP-colour, pixel-art chrome for the cockpit.

Two layers, deliberately kept separate:

1. `.streamlit/config.toml` — the *robust* base (native Streamlit theming:
   SAP Fiori Horizon blue/navy). This always applies; it can't fail to load.
2. `inject()` below — the *decorative* pixel-art layer (chunky borders, hard
   offset shadows, a pixel display font on titles). Pure CSS on top of (1);
   if a browser ever strips injected styles, the app still reads as
   on-brand SAP blue/navy, just without the retro chrome.

`chip()` replaces Streamlit's `:color-badge[...]` markdown directive, which
does not render on every installed Streamlit version — it was leaking
through as literal ":red-badge[...]" text in the queue (see the review that
flagged this). A plain HTML span has no version dependency and gives full
control over the pixel-chip look besides.
"""

from __future__ import annotations

import streamlit as st

# SAP Fiori Horizon palette. Primary blue #0070F2 is SAP's documented
# Horizon "Information/Interactive" colour; navy/semantic tones follow the
# same design system's dark-mode conventions.
SAP_BLUE = "#0070F2"
SAP_NAVY = "#001E50"
SAP_NAVY_LIGHT = "#0A3D75"
SAP_GOLD = "#E9730C"
SAP_RED = "#BB0000"
SAP_GREEN = "#107E3E"
SAP_VIOLET = "#5B3A9E"
SAP_GRAY = "#5A6B7D"

CHIP_COLORS = {
    "red": SAP_RED, "gold": SAP_GOLD, "green": SAP_GREEN,
    "blue": SAP_BLUE, "violet": SAP_VIOLET, "gray": SAP_GRAY,
}


def chip(text: str, color: str = "gray") -> str:
    """A square, chunky-bordered status chip. Caller must render with
    `unsafe_allow_html=True` — this returns raw HTML, not markdown syntax."""
    fg = CHIP_COLORS.get(color, SAP_GRAY)
    return (f'<span style="display:inline-block;font-family:\'VT323\',monospace;'
            f'font-size:16px;line-height:1;padding:3px 9px;margin:1px 0;'
            f'border:2px solid {fg};background:{fg}26;color:{fg};'
            f'border-radius:0;letter-spacing:0.5px;">{text}</span>')


def inject() -> None:
    """Call once near the top of every page. Idempotent — safe to call on
    every rerun; Streamlit dedupes identical injected <style> blocks."""
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&family=VT323&display=swap');

    h1 {{
        font-family:'Press Start 2P', monospace !important;
        font-size:26px !important; line-height:1.7 !important;
        letter-spacing:1px; text-shadow:3px 3px 0 {SAP_NAVY};
    }}
    h2, h3 {{
        font-family:'VT323', monospace !important; font-weight:700 !important;
        letter-spacing:0.5px; color:{SAP_BLUE} !important;
    }}
    html, body, [class*="css"], p, span, div, label {{
        font-family:'VT323', monospace;
    }}

    .stButton>button, .stDownloadButton>button, .stFormSubmitButton>button {{
        border:3px solid {SAP_NAVY} !important; border-radius:0 !important;
        box-shadow:4px 4px 0 {SAP_NAVY} !important;
        font-family:'VT323', monospace !important; font-size:19px !important;
        transition:transform 0.05s, box-shadow 0.05s;
    }}
    .stButton>button:hover, .stDownloadButton>button:hover,
    .stFormSubmitButton>button:hover {{
        transform:translate(2px, 2px); box-shadow:2px 2px 0 {SAP_NAVY} !important;
    }}

    [data-testid="stMetric"] {{
        border:3px solid {SAP_NAVY_LIGHT}; box-shadow:5px 5px 0 {SAP_NAVY_LIGHT};
        padding:12px; background:{SAP_NAVY_LIGHT}22; border-radius:0;
    }}
    div[data-testid="stExpander"], div[data-testid="stForm"],
    div[data-testid="stVerticalBlockBorderWrapper"],
    div[data-testid="stChatMessage"] {{
        border:3px solid {SAP_NAVY_LIGHT} !important; border-radius:0 !important;
    }}
    div[data-testid="stAlert"] {{ border-radius:0 !important; border-width:2px !important; }}

    table {{ border-collapse:collapse !important; }}
    table th {{
        background:{SAP_NAVY_LIGHT} !important; color:{SAP_BLUE} !important;
        border:2px solid {SAP_NAVY} !important; font-family:'VT323', monospace !important;
    }}
    table td {{ border:1px solid {SAP_NAVY_LIGHT} !important; }}

    input, textarea {{ border-radius:0 !important; }}
    </style>
    """, unsafe_allow_html=True)
