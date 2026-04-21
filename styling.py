"""
IASL Crew Planning Portal — styling module.
Central home for CSS, Plotly theme, colour tokens, and small UI helpers.
Light theme, IASL-appropriate.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.io as pio

# ---------------------------------------------------------------------------
# Colour tokens — light theme
# ---------------------------------------------------------------------------
COLORS = {
    "bg":           "#F7F9FC",
    "surface":      "#FFFFFF",
    "surface_alt":  "#F0F4F9",
    "border":       "#E2E8F0",
    "text":         "#1F2937",
    "text_muted":   "#6B7A8F",
    "accent":       "#00857A",
    "accent_soft":  "#E0F2F0",
    "navy":         "#0F2944",
    "green":        "#16A34A",
    "amber":        "#D97706",
    "red":          "#DC2626",
    "blue":         "#2563EB",
    "violet":       "#7C3AED",
}

FLEET_COLORS = {
    "A330":   "#7C3AED",
    "A320":   "#2563EB",
    "ATR72":  "#00857A",
    "DHC8":   "#D97706",
}


def status_color(gap: float) -> str:
    if gap < 1:
        return COLORS["green"]
    if gap < 2:
        return COLORS["amber"]
    return COLORS["red"]


def status_label(gap: float) -> str:
    if gap < 1:
        return "Met"
    if gap < 2:
        return "Short 1"
    return "Short " + str(int(round(gap)))


# ---------------------------------------------------------------------------
# Plotly default template — light
# ---------------------------------------------------------------------------
def register_plotly_theme():
    tpl = go.layout.Template()
    tpl.layout = go.Layout(
        font=dict(family="Inter, -apple-system, Segoe UI, Roboto, sans-serif",
                  size=13, color=COLORS["text"]),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        colorway=[COLORS["accent"], COLORS["blue"], COLORS["violet"],
                  COLORS["amber"], COLORS["green"], COLORS["red"]],
        xaxis=dict(gridcolor=COLORS["border"], zerolinecolor=COLORS["border"],
                   linecolor=COLORS["border"], tickcolor=COLORS["border"]),
        yaxis=dict(gridcolor=COLORS["border"], zerolinecolor=COLORS["border"],
                   linecolor=COLORS["border"], tickcolor=COLORS["border"]),
        legend=dict(bgcolor="rgba(255,255,255,0.85)", bordercolor=COLORS["border"]),
        margin=dict(l=50, r=30, t=50, b=40),
        hoverlabel=dict(bgcolor=COLORS["surface"],
                        bordercolor=COLORS["border"],
                        font=dict(color=COLORS["text"])),
    )
    pio.templates["iasl"] = tpl
    pio.templates.default = "iasl"


# ---------------------------------------------------------------------------
# Global CSS — built by string substitution to avoid f-string brace parsing
# ---------------------------------------------------------------------------
_CSS_TEMPLATE = """
<style>
/* ---- Force light mode regardless of OS dark-mode preference ---- */
:root {
    color-scheme: light only;
}
@media (prefers-color-scheme: dark) {
    .stApp, body, html {
        background: __BG__ !important;
        color: __TEXT__ !important;
        color-scheme: light only !important;
    }
    .stApp * {
        color-scheme: light only;
    }
    /* Undo Streamlit's auto-dark adjustments */
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stSidebar"],
    [data-testid="stToolbar"],
    [data-testid="stMarkdownContainer"] {
        background-color: transparent !important;
        color: __TEXT__ !important;
    }
    /* Force all form controls to light */
    input, textarea, select, button {
        color-scheme: light !important;
        background-color: __SURFACE__ !important;
        color: __TEXT__ !important;
    }
    /* Dataframe cells */
    [data-testid="stDataFrame"] * {
        color: __TEXT__ !important;
    }
    [data-testid="stDataFrame"] [role="cell"],
    [data-testid="stDataFrame"] [role="columnheader"] {
        background-color: __SURFACE__ !important;
    }
    /* Markdown text fallback */
    .stMarkdown, .stMarkdown p, .stMarkdown li, .stMarkdown span,
    .stMarkdown div, .stCaption, label {
        color: __TEXT__ !important;
    }
    /* Expander content */
    details, summary, [data-testid="stExpander"] {
        background-color: __SURFACE__ !important;
        color: __TEXT__ !important;
    }
}

.stApp {
    background: __BG__;
    color: __TEXT__;
}
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, Segoe UI, Roboto, sans-serif;
}
.block-container {
    padding-top: 1.2rem;
    padding-bottom: 3rem;
    padding-left: 2rem;
    padding-right: 2rem;
    max-width: 100%;
}

/* Extra breathing room on very wide monitors */
@media (min-width: 1600px) {
    .block-container {
        padding-left: 3rem;
        padding-right: 3rem;
    }
}

/* Ensure charts, tables, and the tab bar fill the full width */
.main .block-container,
section.main > div {
    max-width: 100% !important;
}
.stTabs [data-baseweb="tab-list"] {
    width: 100%;
}
[data-testid="stDataFrame"],
[data-testid="stPlotlyChart"] {
    width: 100% !important;
}

h1, h2, h3, h4 {
    color: __NAVY__;
    font-weight: 600;
    letter-spacing: -0.01em;
}
h1 { font-size: 1.9rem; }
h2 { font-size: 1.35rem; }
h3 { font-size: 1.1rem; }

.iasl-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
    background: __SURFACE__;
    border: 1px solid __BORDER__;
    border-radius: 14px;
    margin-bottom: 18px;
    box-shadow: 0 2px 10px rgba(15,41,68,0.06);
}
.iasl-brand {
    display: flex; align-items: center; gap: 14px;
}
.iasl-logo {
    width: 42px; height: 42px; border-radius: 10px;
    background: linear-gradient(135deg, __ACCENT__ 0%, #0F5E9C 100%);
    display: flex; align-items: center; justify-content: center;
    color: white; font-weight: 800; font-size: 16px;
    letter-spacing: 0.5px;
    box-shadow: 0 4px 12px rgba(0,133,122,0.25);
}
.iasl-title {
    font-size: 17px; font-weight: 700; color: __NAVY__;
    line-height: 1.1;
}
.iasl-subtitle {
    font-size: 11px; color: __MUTED__;
    text-transform: uppercase; letter-spacing: 1.2px;
}
.iasl-nav-stats {
    display: flex; gap: 28px;
}
.iasl-nav-stat {
    display: flex; flex-direction: column; align-items: flex-end;
}
.iasl-nav-stat-label {
    font-size: 10px; color: __MUTED__;
    text-transform: uppercase; letter-spacing: 1px;
}
.iasl-nav-stat-value {
    font-size: 18px; font-weight: 700; color: __NAVY__;
}

.metric-card {
    background: __SURFACE__;
    border: 1px solid __BORDER__;
    border-radius: 14px;
    padding: 18px 20px;
    transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
    box-shadow: 0 1px 3px rgba(15,41,68,0.04);
}
.metric-card:hover {
    transform: translateY(-2px);
    border-color: __ACCENT__;
    box-shadow: 0 4px 12px rgba(0,133,122,0.1);
}
.metric-label {
    font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px;
    color: __MUTED__; margin-bottom: 6px;
}
.metric-value {
    font-size: 30px; font-weight: 700; color: __NAVY__;
    line-height: 1.1;
}
.metric-delta {
    font-size: 12px; color: __MUTED__; margin-top: 4px;
}

.fleet-card {
    background: __SURFACE__;
    border: 1px solid __BORDER__;
    border-radius: 14px;
    padding: 16px 18px;
    border-left: 4px solid __ACCENT__;
    box-shadow: 0 1px 3px rgba(15,41,68,0.04);
}
.fleet-card.green  { border-left-color: __GREEN__; }
.fleet-card.amber  { border-left-color: __AMBER__; }
.fleet-card.red    { border-left-color: __RED__; }
.fleet-card-title {
    font-size: 15px; font-weight: 700; margin-bottom: 8px;
    color: __NAVY__;
}
.fleet-card-row {
    display: flex; justify-content: space-between;
    font-size: 13px; padding: 3px 0;
    color: __MUTED__;
}
.fleet-card-row span:last-child {
    color: __NAVY__; font-weight: 600;
}

.pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.3px;
}
.pill-green { background: rgba(22,163,74,0.12);  color: __GREEN__; }
.pill-amber { background: rgba(217,119,6,0.12);  color: __AMBER__; }
.pill-red   { background: rgba(220,38,38,0.12);  color: __RED__; }
.pill-blue  { background: rgba(37,99,235,0.12);  color: __BLUE__; }
.pill-violet{ background: rgba(124,58,237,0.12); color: __VIOLET__; }
.pill-teal  { background: rgba(0,133,122,0.12);  color: __ACCENT__; }
.pill-muted { background: rgba(107,122,143,0.12); color: __MUTED__; }

.stTabs [data-baseweb="tab-list"] {
    gap: 4px;
    background: __SURFACE__;
    padding: 6px;
    border-radius: 12px;
    border: 1px solid __BORDER__;
    box-shadow: 0 1px 3px rgba(15,41,68,0.04);
}
.stTabs [data-baseweb="tab"] {
    background: transparent;
    color: __MUTED__;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 500;
    font-size: 13px;
    border: none;
}
.stTabs [aria-selected="true"] {
    background: __ACCENT__ !important;
    color: white !important;
}

.stButton > button {
    background: __SURFACE__;
    color: __NAVY__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
    padding: 8px 16px;
    font-weight: 500;
    transition: all 0.15s ease;
}
.stButton > button:hover {
    border-color: __ACCENT__;
    color: __ACCENT__;
    background: __ACCENT_SOFT__;
}
.stButton > button[kind="primary"] {
    background: __ACCENT__;
    color: white;
    border-color: __ACCENT__;
}
.stButton > button[kind="primary"]:hover {
    background: #009E91;
    color: white;
    border-color: #009E91;
}

.stDownloadButton > button {
    background: __SURFACE__;
    color: __NAVY__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
}
.stDownloadButton > button:hover {
    border-color: __ACCENT__;
    color: __ACCENT__;
    background: __ACCENT_SOFT__;
}

.stSelectbox > div > div, .stTextInput > div > div > input,
.stNumberInput > div > div > input, .stMultiSelect > div > div {
    background: __SURFACE__ !important;
    border: 1px solid __BORDER__ !important;
    color: __TEXT__ !important;
    border-radius: 8px !important;
}

.stDataFrame {
    border: 1px solid __BORDER__;
    border-radius: 10px;
    overflow: hidden;
}

.section-header {
    display: flex; align-items: center; gap: 10px;
    margin: 18px 0 10px 0;
    padding-bottom: 8px;
    border-bottom: 1px solid __BORDER__;
}
.section-header-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: __ACCENT__;
}
.section-header-text {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: __NAVY__;
}

.warn-panel {
    background: rgba(217,119,6,0.06);
    border: 1px solid rgba(217,119,6,0.25);
    border-left: 3px solid __AMBER__;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 6px 0;
    font-size: 13px;
    color: __NAVY__;
}
.error-panel {
    background: rgba(220,38,38,0.06);
    border: 1px solid rgba(220,38,38,0.25);
    border-left: 3px solid __RED__;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 6px 0;
    font-size: 13px;
    color: __NAVY__;
}
.info-panel {
    background: rgba(0,133,122,0.05);
    border: 1px solid rgba(0,133,122,0.2);
    border-left: 3px solid __ACCENT__;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 6px 0;
    font-size: 13px;
    color: __NAVY__;
}

.stProgress > div > div > div > div {
    background: __ACCENT__;
}

.streamlit-expanderHeader {
    background: __SURFACE__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    color: __NAVY__;
}

#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
header { visibility: hidden; }
</style>
"""


def inject_css():
    css = _CSS_TEMPLATE
    replacements = {
        "__BG__":          COLORS["bg"],
        "__SURFACE__":     COLORS["surface"],
        "__SURFACE_ALT__": COLORS["surface_alt"],
        "__BORDER__":      COLORS["border"],
        "__TEXT__":        COLORS["text"],
        "__MUTED__":       COLORS["text_muted"],
        "__ACCENT__":      COLORS["accent"],
        "__ACCENT_SOFT__": COLORS["accent_soft"],
        "__NAVY__":        COLORS["navy"],
        "__GREEN__":       COLORS["green"],
        "__AMBER__":       COLORS["amber"],
        "__RED__":         COLORS["red"],
        "__BLUE__":        COLORS["blue"],
        "__VIOLET__":      COLORS["violet"],
    }
    for key, val in replacements.items():
        css = css.replace(key, val)
    st.markdown(css, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def metric_card(label: str, value, delta: str = ""):
    delta_html = ""
    if delta:
        delta_html = '<div class="metric-delta">' + str(delta) + '</div>'
    html = (
        '<div class="metric-card">'
        '<div class="metric-label">' + str(label) + '</div>'
        '<div class="metric-value">' + str(value) + '</div>'
        + delta_html +
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def fleet_card(fleet: str, requirement: int, available: float,
               aircraft: int, band: str):
    gap = max(0, requirement - available)
    band_class = band if band in ("green", "amber", "red") else "green"
    html = (
        '<div class="fleet-card ' + band_class + '">'
        '<div class="fleet-card-title">' + str(fleet) + '</div>'
        '<div class="fleet-card-row"><span>Aircraft</span><span>' + str(aircraft) + '</span></div>'
        '<div class="fleet-card-row"><span>Required</span><span>' + str(requirement) + '</span></div>'
        '<div class="fleet-card-row"><span>Available</span><span>' + ('%.1f' % available) + '</span></div>'
        '<div class="fleet-card-row"><span>Gap</span>'
        '<span class="pill pill-' + band_class + '">' + ('%.1f' % gap) + '</span></div>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def pill(text: str, kind: str = "muted") -> str:
    return '<span class="pill pill-' + kind + '">' + str(text) + '</span>'


def section_header(text: str):
    html = (
        '<div class="section-header">'
        '<div class="section-header-dot"></div>'
        '<div class="section-header-text">' + str(text) + '</div>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def info_panel(text: str, kind: str = "info"):
    cls_map = {"info": "info-panel", "warn": "warn-panel", "error": "error-panel"}
    cls = cls_map.get(kind, "info-panel")
    html = '<div class="' + cls + '">' + str(text) + '</div>'
    st.markdown(html, unsafe_allow_html=True)
