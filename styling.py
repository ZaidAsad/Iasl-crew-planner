"""
IASL Crew Planning Portal — styling module.
Central home for CSS, Plotly theme, colour tokens, and small UI helpers.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.io as pio

# ---------------------------------------------------------------------------
# Colour tokens
# ---------------------------------------------------------------------------
COLORS = {
    "bg":           "#0B1220",   # app background
    "surface":      "#111A2E",   # card background
    "surface_alt":  "#18243D",   # raised card / hover
    "border":       "#22304A",
    "text":         "#E6ECF5",
    "text_muted":   "#8FA0BD",
    "accent":       "#00B3A6",   # IASL-style teal
    "accent_soft":  "#0E3A38",
    "green":        "#22C55E",
    "amber":        "#F59E0B",
    "red":          "#EF4444",
    "blue":         "#3B82F6",
    "violet":       "#8B5CF6",
}

FLEET_COLORS = {
    "A330":   "#8B5CF6",
    "A320":   "#3B82F6",
    "ATR72":  "#00B3A6",
    "DHC8":   "#F59E0B",
}


def status_color(gap: float) -> str:
    """Return the status colour for a given numeric gap (requirement - availability)."""
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
    return f"Short {int(round(gap))}"


# ---------------------------------------------------------------------------
# Plotly default template
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
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor=COLORS["border"]),
        margin=dict(l=50, r=30, t=50, b=40),
        hoverlabel=dict(bgcolor=COLORS["surface_alt"],
                        bordercolor=COLORS["border"],
                        font=dict(color=COLORS["text"])),
    )
    pio.templates["iasl"] = tpl
    pio.templates.default = "iasl"


# ---------------------------------------------------------------------------
# Global CSS
# ---------------------------------------------------------------------------
def inject_css():
    css = f"""
    <style>
    /* ---- Base ---- */
    .stApp {{
        background: linear-gradient(180deg, {COLORS['bg']} 0%, #0A0F1C 100%);
        color: {COLORS['text']};
    }}
    html, body, [class*="css"] {{
        font-family: 'Inter', -apple-system, Segoe UI, Roboto, sans-serif;
    }}
    .block-container {{
        padding-top: 1.2rem;
        padding-bottom: 3rem;
        max-width: 1400px;
    }}

    /* ---- Headings ---- */
    h1, h2, h3, h4 {{
        color: {COLORS['text']};
        font-weight: 600;
        letter-spacing: -0.01em;
    }}
    h1 {{ font-size: 1.9rem; }}
    h2 {{ font-size: 1.35rem; }}
    h3 {{ font-size: 1.1rem; }}

    /* ---- Top nav bar ---- */
    .iasl-topbar {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 14px 20px;
        background: {COLORS['surface']};
        border: 1px solid {COLORS['border']};
        border-radius: 14px;
        margin-bottom: 18px;
        box-shadow: 0 4px 18px rgba(0,0,0,0.25);
    }}
    .iasl-brand {{
        display: flex; align-items: center; gap: 14px;
    }}
    .iasl-logo {{
        width: 42px; height: 42px; border-radius: 10px;
        background: linear-gradient(135deg, {COLORS['accent']} 0%, #0077B6 100%);
        display: flex; align-items: center; justify-content: center;
        color: white; font-weight: 800; font-size: 16px;
        letter-spacing: 0.5px;
        box-shadow: 0 4px 12px rgba(0,179,166,0.3);
    }}
    .iasl-title {{
        font-size: 17px; font-weight: 700; color: {COLORS['text']};
        line-height: 1.1;
    }}
    .iasl-subtitle {{
        font-size: 11px; color: {COLORS['text_muted']};
        text-transform: uppercase; letter-spacing: 1.2px;
    }}
    .iasl-nav-stats {{
        display: flex; gap: 28px;
    }}
    .iasl-nav-stat {{
        display: flex; flex-direction: column; align-items: flex-end;
    }}
    .iasl-nav-stat-label {{
        font-size: 10px; color: {COLORS['text_muted']};
        text-transform: uppercase; letter-spacing: 1px;
    }}
    .iasl-nav-stat-value {{
        font-size: 18px; font-weight: 700; color: {COLORS['text']};
    }}

    /* ---- Metric cards ---- */
    .metric-card {{
        background: {COLORS['surface']};
        border: 1px solid {COLORS['border']};
        border-radius: 14px;
        padding: 18px 20px;
        transition: transform 0.15s ease, border-color 0.15s ease;
    }}
    .metric-card:hover {{
        transform: translateY(-2px);
        border-color: {COLORS['accent']};
    }}
    .metric-label {{
        font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px;
        color: {COLORS['text_muted']}; margin-bottom: 6px;
    }}
    .metric-value {{
        font-size: 30px; font-weight: 700; color: {COLORS['text']};
        line-height: 1.1;
    }}
    .metric-delta {{
        font-size: 12px; color: {COLORS['text_muted']}; margin-top: 4px;
    }}

    /* ---- Fleet status cards ---- */
    .fleet-card {{
        background: {COLORS['surface']};
        border: 1px solid {COLORS['border']};
        border-radius: 14px;
        padding: 16px 18px;
        border-left: 4px solid {COLORS['accent']};
    }}
    .fleet-card.green  {{ border-left-color: {COLORS['green']}; }}
    .fleet-card.amber  {{ border-left-color: {COLORS['amber']}; }}
    .fleet-card.red    {{ border-left-color: {COLORS['red']}; }}
    .fleet-card-title {{
        font-size: 15px; font-weight: 700; margin-bottom: 8px;
    }}
    .fleet-card-row {{
        display: flex; justify-content: space-between;
        font-size: 13px; padding: 3px 0;
        color: {COLORS['text_muted']};
    }}
    .fleet-card-row span:last-child {{
        color: {COLORS['text']}; font-weight: 600;
    }}

    /* ---- Status pills ---- */
    .pill {{
        display: inline-block;
        padding: 3px 10px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.3px;
    }}
    .pill-green {{ background: rgba(34,197,94,0.15); color: {COLORS['green']}; }}
    .pill-amber {{ background: rgba(245,158,11,0.15); color: {COLORS['amber']}; }}
    .pill-red   {{ background: rgba(239,68,68,0.15);  color: {COLORS['red']}; }}
    .pill-blue  {{ background: rgba(59,130,246,0.15); color: {COLORS['blue']}; }}
    .pill-violet{{ background: rgba(139,92,246,0.15); color: {COLORS['violet']}; }}
    .pill-teal  {{ background: rgba(0,179,166,0.15);  color: {COLORS['accent']}; }}
    .pill-muted {{ background: rgba(143,160,189,0.12); color: {COLORS['text_muted']}; }}

    /* ---- Streamlit widget overrides ---- */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        background: {COLORS['surface']};
        padding: 6px;
        border-radius: 12px;
        border: 1px solid {COLORS['border']};
    }}
    .stTabs [data-baseweb="tab"] {{
        background: transparent;
        color: {COLORS['text_muted']};
        border-radius: 8px;
        padding: 8px 16px;
        font-weight: 500;
        font-size: 13px;
        border: none;
    }}
    .stTabs [aria-selected="true"] {{
        background: {COLORS['accent']} !important;
        color: white !important;
    }}

    .stButton > button {{
        background: {COLORS['surface_alt']};
        color: {COLORS['text']};
        border: 1px solid {COLORS['border']};
        border-radius: 10px;
        padding: 8px 16px;
        font-weight: 500;
        transition: all 0.15s ease;
    }}
    .stButton > button:hover {{
        border-color: {COLORS['accent']};
        color: {COLORS['accent']};
    }}
    .stButton > button[kind="primary"] {{
        background: {COLORS['accent']};
        color: white;
        border-color: {COLORS['accent']};
    }}
    .stButton > button[kind="primary"]:hover {{
        background: #00D4C5;
        color: white;
    }}

    .stDownloadButton > button {{
        background: {COLORS['surface_alt']};
        color: {COLORS['text']};
        border: 1px solid {COLORS['border']};
        border-radius: 10px;
    }}
    .stDownloadButton > button:hover {{
        border-color: {COLORS['accent']};
        color: {COLORS['accent']};
    }}

    .stSelectbox > div > div, .stTextInput > div > div > input,
    .stNumberInput > div > div > input, .stMultiSelect > div > div {{
        background: {COLORS['surface']} !important;
        border: 1px solid {COLORS['border']} !important;
        color: {COLORS['text']} !important;
        border-radius: 8px !important;
    }}

    .stDataFrame {{
        border: 1px solid {COLORS['border']};
        border-radius: 10px;
        overflow: hidden;
    }}

    /* Section headers */
    .section-header {{
        display: flex; align-items: center; gap: 10px;
        margin: 18px 0 10px 0;
        padding-bottom: 8px;
        border-bottom: 1px solid {COLORS['border']};
    }}
    .section-header-dot {{
        width: 8px; height: 8px; border-radius: 50%;
        background: {COLORS['accent']};
    }}
    .section-header-text {{
        font-size: 13px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1.2px;
        color: {COLORS['text']};
    }}

    /* Warning / info panels */
    .warn-panel {{
        background: rgba(245,158,11,0.08);
        border: 1px solid rgba(245,158,11,0.3);
        border-left: 3px solid {COLORS['amber']};
        border-radius: 8px;
        padding: 10px 14px;
        margin: 6px 0;
        font-size: 13px;
    }}
    .error-panel {{
        background: rgba(239,68,68,0.08);
        border: 1px solid rgba(239,68,68,0.3);
        border-left: 3px solid {COLORS['red']};
        border-radius: 8px;
        padding: 10px 14px;
        margin: 6px 0;
        font-size: 13px;
    }}
    .info-panel {{
        background: rgba(0,179,166,0.06);
        border: 1px solid rgba(0,179,166,0.25);
        border-left: 3px solid {COLORS['accent']};
        border-radius: 8px;
        padding: 10px 14px;
        margin: 6px 0;
        font-size: 13px;
    }}

    /* Hide Streamlit chrome */
    #MainMenu {{ visibility: hidden; }}
    footer {{ visibility: hidden; }}
    header {{ visibility: hidden; }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def metric_card(label: str, value, delta: str = ""):
    delta_html = f'<div class="metric-delta">{delta}</div>' if delta else ""
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def fleet_card(fleet: str, requirement: int, available: float,
               aircraft: int, band: str):
    gap = max(0, requirement - available)
    band_class = band if band in ("green", "amber", "red") else "green"
    st.markdown(
        f"""
        <div class="fleet-card {band_class}">
            <div class="fleet-card-title">{fleet}</div>
            <div class="fleet-card-row"><span>Aircraft</span><span>{aircraft}</span></div>
            <div class="fleet-card-row"><span>Required</span><span>{requirement}</span></div>
            <div class="fleet-card-row"><span>Available</span><span>{available:.1f}</span></div>
            <div class="fleet-card-row"><span>Gap</span>
                <span class="pill pill-{band_class}">{gap:.1f}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def pill(text: str, kind: str = "muted") -> str:
    return f'<span class="pill pill-{kind}">{text}</span>'


def section_header(text: str):
    st.markdown(
        f'<div class="section-header">'
        f'<div class="section-header-dot"></div>'
        f'<div class="section-header-text">{text}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def info_panel(text: str, kind: str = "info"):
    cls = {"info": "info-panel", "warn": "warn-panel", "error": "error-panel"}[kind]
    st.markdown(f'<div class="{cls}">{text}</div>', unsafe_allow_html=True)