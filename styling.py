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
    "bg":           "#F7F9FC",   # app background (subtle cool grey)
    "surface":      "#FFFFFF",   # card background
    "surface_alt":  "#F0F4F9",   # raised card / hover
    "border":       "#E2E8F0",
    "text":         "#1F2937",
    "text_muted":   "#6B7A8F",
    "accent":       "#00857A",   # IASL teal (deeper for light bg)
    "accent_soft":  "#E0F2F0",
    "navy":         "#0F2944",   # used for dark text/accents
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
    return f"Short {int(round(gap))}"


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
# Global CSS — light theme
# ---------------------------------------------------------------------------
def inject_css():
    css = f"""
    <style>
    /* ---- Base ---- */
    .stApp {{
        background: {COLORS['bg']};
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
        color: {COLORS['navy']};
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
        box-shadow: 0 2px 10px rgba(15,41,68,0.06);
    }}
    .iasl-brand {{
        display: flex; align-items: center; gap: 14px;
    }}
    .iasl-logo {{
        width: 42px; height: 42px; border-radius: 10px;
        background: linear-gradient(135deg, {COLORS['accent']} 0%, #0F5E9C 100%);
        display: flex; align-items: center; justify-content: center;
        color: white; font-weight: 800; font-size: 16px;
        letter-spacing: 0.5px;
        box-shadow: 0 4px 12px rgba(0,133,122,0.25);
    }}
    .iasl-title {{
        font-size: 17px; font-weight: 700; color: {COLORS['navy']};
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
        font-size: 18px; font-weight: 700; color: {COLORS['navy']};
    }}

    /* ---- Metric cards ---- */
    .metric-card {{
        background: {COLORS['surface']};
        border: 1px solid {COLORS['border']};
        border-radius: 14px;
        padding: 18px 20px;
        transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
        box-shadow: 0 1px 3px rgba(15,41,68,0.04);
    }}
    .metric-card:hover {{
        transform: translateY(-2px);
        border-color: {COLORS['accent']};
        box-shadow: 0 4px 12px rgba(0,133,122,0.1);
    }}
    .metric-label {{
        font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px;
        color: {COLORS['text_muted']}; margin-bottom: 6px;
    }}
    .metric-value {{
        font-size: 30px; font-weight: 700; color: {COLORS['navy']};
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
        box-shadow: 0 1px 3px rgba(15,41,68,0.04);
    }}
    .fleet-card.green  {{ border-left-color: {COLORS['green']}; }}
    .fleet-card.amber  {{ border-left-color: {COLORS['amber']}; }}
    .fleet-card.red    {{ border-left-color: {COLORS['red']}; }}
    .fleet-card-title {{
        font-size: 15px; font-weight: 700; margin-bottom: 8px;
        color: {COLORS['navy']};
    }}
    .fleet-card-row {{
        display: flex; justify-content: space-between;
        font-size: 13px; padding: 3px 0;
        color: {COLORS['text_muted']};
    }}
    .fleet-card-row span:last-child {{
        color: {COLORS['navy']}; font-weight: 600;
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
    .pill-green {{ background: rgba(22,163,74,0.12);  color: {COLORS['green']}; }}
    .pill-amber {{ background: rgba(217,119,6,0.12);  color: {COLORS['amber']}; }}
    .pill-red   {{ background: rgba(220,38,38,0.12);  color: {COLORS['red']}; }}
    .pill-blue  {{ background: rgba(37,99,235,0.12);  color: {COLORS['blue']}; }}
    .pill-violet{{ background: rgba(124,58,237,0.12); color: {COLORS['violet']}; }}
    .pill-teal  {{ background: rgba(0,133,122,0.12);  color: {COLORS['accent']}; }}
    .pill-muted {{ background: rgba(107,122,143,0.12); color: {COLORS['text_muted']}; }}

    /* ---- Streamlit widget overrides ---- */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        background: {COLORS['surface']};
        padding: 6px;
        border-radius: 12px;
        border: 1px solid {COLORS['border']};
        box-shadow: 0 1px 3px rgba(15,41,68,0.04);
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
        background: {COLORS['surface']};
        color: {COLORS['navy']};
        border: 1px solid {COLORS['border']};
        border-radius: 10px;
        padding: 8px 16px;
        font-weight: 500;
        transition: all 0.15s ease;
    }}
    .stButton > button:hover {{
        border-color: {COLORS['accent']};
        color: {COLORS['accent']};
        background: {COLORS['accent_soft']};
    }}
    .stButton > button[kind="primary"] {{
        background: {COLORS['accent']};
        color: white;
        border-color: {COLORS['accent']};
    }}
    .stButton > button[kind="primary"]:hover {{
        background: #009E91;
        color: white;
        border-color: #009E91;
    }}

    .stDownloadButton > button {{
        background: {COLORS['surface']};
        color: {COLORS['navy']};
        border: 1px solid {COLORS['border']};
        border-radius: 10px;
    }}
    .stDownloadButton > button:hover {{
        border-color: {COLORS['accent']};
        color: {COLORS['accent']};
        background: {COLORS['accent_soft']};
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
        color: {COLORS['navy']};
    }}

    /* Warning / info panels */
    .warn-panel {{
        background: rgba(217,119,6,0.06);
        border: 1px solid rgba(217,119,6,0.25);
        border-left: 3px solid {COLORS['amber']};
        border-radius: 8px;
        padding: 10px 14px;
        margin: 6px 0;
        font-size: 13px;
        color: {COLORS['navy']};
    }}
    .error-panel {{
        background: rgba(220,38,38,0.06);
        border: 1px solid rgba(220,38,38,0.25);
        border-left: 3px solid {COLORS['red']};
        border-radius: 8px;
        padding: 10px 14px;
        margin: 6px 0;
        font-size: 13px;
        color: {COLORS['navy']};
    }}
    .info-panel {{
        background: rgba(0,133,122,0.05);
        border: 1px solid rgba(0,133,122,0.2);
        border-left: 3px solid {COLORS['accent']};
        border-radius: 8px;
        padding: 10px 14px;
        margin: 6px 0;
        font-size: 13px;
        color: {COLORS['navy']};
    }}

    /* Progress bars */
    .stProgress > div > div > div > div {{
        background: {COLORS['accent']};
    }}

    /* Expanders */
    .streamlit-expanderHeader {{
        background: {COLORS['surface']};
        border: 1px solid {COLORS['border']};
        border-radius: 8px;
        color: {COLORS['navy']};
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
    st.markdown(f'<div class="{cls}">{text}</div>', unsafe_allow_html=Tr
