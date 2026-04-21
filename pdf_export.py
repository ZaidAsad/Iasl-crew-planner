"""
IASL Crew Planning Portal — PDF export module.

Produces a print-ready A4 landscape PDF using ReportLab. Two modes:
  - "executive": 8-12 pages, headline visuals + summary tables
  - "comprehensive": 20-30 pages, every cohort, every pilot, every cascade

Visual language:
  - Fleet transition network graph (NetworkX layout)
  - Pilot journey graph per training cohort
  - Timeline Gantt chart of all actions
  - Gap heatmap, localisation curve, cost curves
  - Polished tables with proper cell wrapping and alignment
"""

from __future__ import annotations

import io
import math
from datetime import date
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, KeepTogether, CondPageBreak,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

from cascade_engine import (
    FLEETS, FUNCTIONS, CREW_SETS_PER_AIRCRAFT,
    Pilot, PlannedAction, FleetChange,
    month_labels, resolve_aircraft_counts, fleet_requirement,
    compute_availability, compute_gaps, gap_band,
    localisation_summary,
)

# ---------------------------------------------------------------------------
# Design tokens — must match app styling
# ---------------------------------------------------------------------------
C_NAVY    = colors.HexColor("#0F2944")
C_ACCENT  = colors.HexColor("#00857A")
C_MUTED   = colors.HexColor("#6B7A8F")
C_BORDER  = colors.HexColor("#E2E8F0")
C_BG      = colors.HexColor("#F7F9FC")
C_SURFACE = colors.HexColor("#FFFFFF")
C_GREEN   = colors.HexColor("#16A34A")
C_AMBER   = colors.HexColor("#D97706")
C_RED     = colors.HexColor("#DC2626")
C_BLUE    = colors.HexColor("#2563EB")
C_VIOLET  = colors.HexColor("#7C3AED")

# Matplotlib hex equivalents
MPL = {
    "navy": "#0F2944", "accent": "#00857A", "muted": "#6B7A8F",
    "border": "#E2E8F0", "bg": "#F7F9FC", "surface": "#FFFFFF",
    "green": "#16A34A", "amber": "#D97706", "red": "#DC2626",
    "blue": "#2563EB", "violet": "#7C3AED",
}

FLEET_HEX = {
    "A330":  "#7C3AED",
    "A320":  "#2563EB",
    "ATR72": "#00857A",
    "DHC8":  "#D97706",
}


# ---------------------------------------------------------------------------
# Paragraph styles
# ---------------------------------------------------------------------------
def _styles():
    s = getSampleStyleSheet()

    def add(name, **kw):
        if name in s.byName:
            # Overwrite
            for k, v in kw.items():
                setattr(s[name], k, v)
        else:
            base = kw.pop("parent", s["BodyText"])
            s.add(ParagraphStyle(name=name, parent=base, **kw))

    add("Cover", fontName="Helvetica-Bold", fontSize=32, textColor=C_NAVY,
        leading=38, alignment=TA_CENTER, spaceAfter=14)
    add("CoverSub", fontName="Helvetica", fontSize=14, textColor=C_MUTED,
        leading=20, alignment=TA_CENTER, spaceAfter=6)
    add("CoverMeta", fontName="Helvetica", fontSize=10, textColor=C_MUTED,
        leading=14, alignment=TA_CENTER)
    add("H1", fontName="Helvetica-Bold", fontSize=18, textColor=C_NAVY,
        leading=22, spaceAfter=8, spaceBefore=2)
    add("H2", fontName="Helvetica-Bold", fontSize=13, textColor=C_NAVY,
        leading=16, spaceAfter=6, spaceBefore=10)
    add("H3", fontName="Helvetica-Bold", fontSize=10.5, textColor=C_ACCENT,
        leading=13, spaceAfter=4, spaceBefore=6)
    add("Body", fontName="Helvetica", fontSize=9.5, textColor=C_NAVY,
        leading=13, spaceAfter=4, alignment=TA_LEFT)
    add("BodySm", fontName="Helvetica", fontSize=8.5, textColor=C_NAVY,
        leading=11, spaceAfter=3, alignment=TA_LEFT)
    add("Caption", fontName="Helvetica-Oblique", fontSize=8, textColor=C_MUTED,
        leading=11, spaceAfter=2, alignment=TA_LEFT)
    add("CellLeft", fontName="Helvetica", fontSize=8.5, textColor=C_NAVY,
        leading=11, alignment=TA_LEFT)
    add("CellCenter", fontName="Helvetica", fontSize=8.5, textColor=C_NAVY,
        leading=11, alignment=TA_CENTER)
    add("CellRight", fontName="Helvetica", fontSize=8.5, textColor=C_NAVY,
        leading=11, alignment=TA_RIGHT)
    add("CellHead", fontName="Helvetica-Bold", fontSize=8.5, textColor=C_SURFACE,
        leading=11, alignment=TA_CENTER)
    return s


# ---------------------------------------------------------------------------
# Page frame — header, footer
# ---------------------------------------------------------------------------
def _on_page(canvas, doc):
    canvas.saveState()
    w, h = landscape(A4)
    # Header line
    canvas.setStrokeColor(C_BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(1.5 * cm, h - 1.1 * cm, w - 1.5 * cm, h - 1.1 * cm)

    canvas.setFillColor(C_ACCENT)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(1.5 * cm, h - 0.85 * cm, "IASL CREW PLANNING PORTAL")

    canvas.setFillColor(C_MUTED)
    canvas.setFont("Helvetica", 8)
    title = getattr(doc, "doc_title", "Crew Plan")
    canvas.drawCentredString(w / 2, h - 0.85 * cm, title)
    canvas.drawRightString(w - 1.5 * cm, h - 0.85 * cm,
                           date.today().strftime("%d %b %Y"))

    # Footer
    canvas.setStrokeColor(C_BORDER)
    canvas.line(1.5 * cm, 1.1 * cm, w - 1.5 * cm, 1.1 * cm)
    canvas.setFillColor(C_MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(1.5 * cm, 0.7 * cm,
                      "Confidential — Flight Operations, Island Aviation Services Limited")
    canvas.drawRightString(w - 1.5 * cm, 0.7 * cm,
                           f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Helpers for text wrapping in cells
# ---------------------------------------------------------------------------
def _P(text: str, style_name: str = "CellLeft", styles=None) -> Paragraph:
    """Wrap text in a Paragraph so it wraps inside table cells."""
    styles = styles or _styles()
    if text is None:
        text = ""
    # Escape reserved characters for ReportLab markup
    safe = (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))
    return Paragraph(safe, styles[style_name])


def _color_for_band(band: str):
    return {"green": C_GREEN, "amber": C_AMBER, "red": C_RED}.get(band, C_MUTED)


# ---------------------------------------------------------------------------
# Matplotlib renderers — all return BytesIO PNGs for Image()
# ---------------------------------------------------------------------------
def _fig_to_png(fig, dpi=200) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


def _render_gap_heatmap(req, avail, labels) -> io.BytesIO:
    rows = []
    z = []
    for f in FLEETS:
        for fn in FUNCTIONS:
            rows.append(f"{f} · {fn[:3]}")
            row = []
            for i in range(len(labels)):
                gap = max(0, req[f][fn][i] - avail[f][fn][i])
                row.append(0 if gap < 1 else (1 if gap < 2 else 2))
            z.append(row)

    fig, ax = plt.subplots(figsize=(11, 3.2))
    cmap = matplotlib.colors.ListedColormap(
        [MPL["green"], MPL["amber"], MPL["red"]]
    )
    im = ax.imshow(z, aspect="auto", cmap=cmap, vmin=0, vmax=2)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7,
                       color=MPL["navy"])
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(rows, fontsize=8, color=MPL["navy"])
    ax.set_title("Requirement gaps across planning horizon",
                 color=MPL["navy"], fontsize=11, pad=10, fontweight="bold")

    # Legend patches
    patches = [
        mpatches.Patch(color=MPL["green"], label="Met"),
        mpatches.Patch(color=MPL["amber"], label="1 short"),
        mpatches.Patch(color=MPL["red"], label="2+ short"),
    ]
    ax.legend(handles=patches, loc="upper center",
              bbox_to_anchor=(0.5, -0.28), ncol=3, frameon=False, fontsize=8)

    for spine in ax.spines.values():
        spine.set_color(MPL["border"])
    plt.tight_layout()
    return _fig_to_png(fig)


def _render_req_vs_avail(req, avail, labels) -> io.BytesIO:
    fig, axes = plt.subplots(2, 2, figsize=(11, 5.5), sharex=True)
    axes = axes.flatten()
    x = list(range(len(labels)))
    for i, f in enumerate(FLEETS):
        ax = axes[i]
        for fn in FUNCTIONS:
            color = FLEET_HEX[f]
            if fn == "First Officer":
                rgb = matplotlib.colors.to_rgb(color)
                color = matplotlib.colors.to_hex(
                    tuple(c + (1 - c) * 0.4 for c in rgb)
                )
            ax.plot(x, req[f][fn], linestyle="--", color=color, linewidth=1.2,
                    alpha=0.8, label=f"{fn[:3]} req.")
            ax.plot(x, avail[f][fn], linestyle="-", color=color, linewidth=2,
                    marker="o", markersize=3, label=f"{fn[:3]} avail.")
            ax.fill_between(x, 0, avail[f][fn], color=color, alpha=0.1)
        ax.set_title(f, color=MPL["navy"], fontsize=10, fontweight="bold")
        ax.tick_params(colors=MPL["muted"], labelsize=7)
        ax.grid(True, linestyle=":", color=MPL["border"], linewidth=0.5)
        ax.legend(fontsize=6, loc="best", frameon=False)
        for spine in ax.spines.values():
            spine.set_color(MPL["border"])
    # X labels only on bottom row
    for ax in axes[2:]:
        ax.set_xticks(x[::max(1, len(x) // 8)])
        ax.set_xticklabels([labels[i] for i in x[::max(1, len(x) // 8)]],
                           rotation=45, ha="right", fontsize=7)
    fig.suptitle("Requirement vs availability by fleet",
                 color=MPL["navy"], fontsize=12, fontweight="bold", y=1.02)
    plt.tight_layout()
    return _fig_to_png(fig)


def _render_localisation_curve(state, labels, horizon) -> io.BytesIO:
    """Projected local % over the horizon."""
    loc = localisation_summary(state["pilots"])
    local, total = loc["local"], loc["total"]

    # Termination lookup
    pilot_by_id = {p.employee_id: p for p in state["pilots"]}
    term_nat: list[tuple[int, str]] = []
    for a in state["actions"]:
        if a.action_type == "Pilot Termination":
            for tid in a.trainee_ids:
                if tid.startswith("TBD"): continue
                p = pilot_by_id.get(tid)
                if p:
                    term_nat.append((a.start_month, p.nationality))

    series = []
    for m in range(horizon):
        for a in state["actions"]:
            end = a.start_month + a.duration
            if end == m:
                if a.action_type in ("Cadet Hire", "Local Hire"):
                    local += 1; total += 1
                elif a.action_type == "Expat Hire":
                    total += 1
        for tm, nat in term_nat:
            if tm == m:
                if nat == "Local": local = max(0, local - 1)
                total = max(0, total - 1)
        series.append((local / total * 100) if total else 0)

    fig, ax = plt.subplots(figsize=(11, 3))
    x = list(range(horizon))
    ax.plot(x, series, color=MPL["accent"], linewidth=2.5, marker="o",
            markersize=4)
    ax.fill_between(x, 0, series, color=MPL["accent"], alpha=0.15)
    ax.axhline(y=80, linestyle=":", color=MPL["green"], alpha=0.6, linewidth=1)
    ax.text(len(x) - 1, 80, " 80% target", fontsize=7, color=MPL["green"],
            va="center")
    ax.set_ylim(0, 105)
    ax.set_ylabel("Local %", fontsize=9, color=MPL["navy"])
    ax.set_xticks(x[::max(1, len(x) // 10)])
    ax.set_xticklabels([labels[i] for i in x[::max(1, len(x) // 10)]],
                       rotation=45, ha="right", fontsize=7)
    ax.tick_params(colors=MPL["muted"], labelsize=7)
    ax.set_title("Projected localisation over horizon",
                 color=MPL["navy"], fontsize=11, fontweight="bold", pad=10)
    ax.grid(True, linestyle=":", color=MPL["border"], linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_color(MPL["border"])
    plt.tight_layout()
    return _fig_to_png(fig)


def _render_cost_curves(state, labels, horizon) -> io.BytesIO | None:
    """Cumulative cost by currency."""
    cost_by_month: dict[str, list[float]] = {}
    for a in state["actions"]:
        cost = getattr(a, "cost", 0.0) or 0.0
        cur = getattr(a, "cost_currency", "USD") or "USD"
        if cost <= 0: continue
        if cur not in cost_by_month:
            cost_by_month[cur] = [0.0] * horizon
        if 0 <= a.start_month < horizon:
            cost_by_month[cur][a.start_month] += cost

    if not cost_by_month:
        return None

    cumulative = {cur: list(np.cumsum(vals)) for cur, vals in cost_by_month.items()}

    fig, ax = plt.subplots(figsize=(11, 3))
    x = list(range(horizon))
    palette = [MPL["accent"], MPL["violet"], MPL["amber"], MPL["blue"]]
    for i, (cur, vals) in enumerate(cumulative.items()):
        color = palette[i % len(palette)]
        ax.plot(x, vals, color=color, linewidth=2.2, marker="o", markersize=3,
                label=f"{cur} cumulative")
        ax.fill_between(x, 0, vals, color=color, alpha=0.1)

    ax.set_ylabel("Cumulative cost", fontsize=9, color=MPL["navy"])
    ax.set_xticks(x[::max(1, len(x) // 10)])
    ax.set_xticklabels([labels[i] for i in x[::max(1, len(x) // 10)]],
                       rotation=45, ha="right", fontsize=7)
    ax.tick_params(colors=MPL["muted"], labelsize=7)
    ax.set_title("Cumulative programme cost",
                 color=MPL["navy"], fontsize=11, fontweight="bold", pad=10)
    ax.legend(fontsize=8, frameon=False, loc="upper left")
    ax.grid(True, linestyle=":", color=MPL["border"], linewidth=0.5)

    # Format Y axis with commas
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}")
    )
    for spine in ax.spines.values():
        spine.set_color(MPL["border"])
    plt.tight_layout()
    return _fig_to_png(fig)


def _render_fleet_network_graph(state, labels) -> io.BytesIO:
    """
    Hub-and-spoke graph: nodes = (fleet × function), edges = planned transitions.
    Edge thickness = trainee count.
    """
    fig, ax = plt.subplots(figsize=(11, 6.5))
    ax.set_xlim(-1.5, 1.5)
    ax.set_ylim(-1.4, 1.4)
    ax.set_aspect("equal")
    ax.axis("off")

    # Layout nodes on two rings
    nodes = {}
    for i, f in enumerate(FLEETS):
        for fn in FUNCTIONS:
            angle = 2 * math.pi * i / len(FLEETS) - math.pi / 2
            r = 1.0 if fn == "Captain" else 0.55
            nodes[(f, fn)] = (r * math.cos(angle), r * math.sin(angle))

    # Ring guides
    for r, lbl in [(1.0, "Captains"), (0.55, "First Officers")]:
        theta = np.linspace(0, 2 * math.pi, 100)
        ax.plot(r * np.cos(theta), r * np.sin(theta),
                linestyle=":", color=MPL["border"], linewidth=0.8, alpha=0.7)
        ax.text(0, r + 0.08, lbl, ha="center", fontsize=7,
                color=MPL["muted"], style="italic")

    # Count edges
    edge_counts: dict[tuple[tuple[str, str], tuple[str, str]], int] = {}
    edge_notes: dict[tuple[tuple[str, str], tuple[str, str]], list[str]] = {}

    for a in state["actions"]:
        trainees_real = [t for t in a.trainee_ids
                         if not t.startswith("SEAT:") and not t.startswith("TBD")]
        tbds = [t for t in a.trainee_ids if t.startswith("TBD")]
        count = len(trainees_real) + len(tbds)
        if count == 0 and a.action_type not in ("Cadet Hire", "Local Hire", "Expat Hire"):
            continue

        if a.action_type == "Type Rating":
            src = (a.from_fleet, a.from_function)
            dst = (a.to_fleet, a.to_function)
        elif a.action_type == "Command Upgrade":
            src = (a.from_fleet or a.to_fleet, "First Officer")
            dst = (a.to_fleet, "Captain")
        else:
            continue

        if src not in nodes or dst not in nodes: continue
        key = (src, dst)
        edge_counts[key] = edge_counts.get(key, 0) + count
        mo = labels[a.start_month] if 0 <= a.start_month < len(labels) else f"M{a.start_month}"
        edge_notes.setdefault(key, []).append(f"{mo}: {a.action_type}")

    # Draw edges as curved arrows
    for (src, dst), count in edge_counts.items():
        x0, y0 = nodes[src]
        x1, y1 = nodes[dst]
        # Curve via midpoint offset
        mx = (x0 + x1) / 2 + (y1 - y0) * 0.22
        my = (y0 + y1) / 2 - (x1 - x0) * 0.22
        width = min(4.5, 1.2 + count * 0.5)
        arrow = FancyArrowPatch(
            (x0, y0), (x1, y1),
            connectionstyle=f"arc3,rad=0.22",
            arrowstyle="->,head_length=8,head_width=6",
            color=MPL["accent"], linewidth=width, alpha=0.6, zorder=1,
        )
        ax.add_patch(arrow)
        # Count label on midpoint
        ax.add_patch(mpatches.FancyBboxPatch(
            (mx - 0.07, my - 0.04), 0.14, 0.08,
            boxstyle="round,pad=0.02",
            facecolor="white", edgecolor=MPL["accent"], linewidth=1.2,
            zorder=2,
        ))
        ax.text(mx, my, str(count), ha="center", va="center",
                fontsize=8, color=MPL["navy"], fontweight="bold", zorder=3)

    # Draw nodes
    req = fleet_requirement(resolve_aircraft_counts(
        state["initial_aircraft"], state["fleet_changes"], state["horizon"]))
    avail = compute_availability(state["pilots"], state["actions"], state["horizon"])

    for (f, fn), (x, y) in nodes.items():
        av = avail[f][fn][-1] if avail[f][fn] else 0
        re = req[f][fn][-1] if req[f][fn] else 0
        gap = max(0, re - av)
        band_color = _band_hex(gap)
        size = max(0.12, 0.10 + 0.02 * min(10, av))

        # Halo
        circle = plt.Circle((x, y), size * 1.4, color=band_color, alpha=0.2, zorder=1)
        ax.add_patch(circle)
        # Node body
        circle = plt.Circle((x, y), size, color=FLEET_HEX[f], alpha=0.92,
                            zorder=3, edgecolor="white", linewidth=2)
        ax.add_patch(circle)

        # Label
        ax.text(x, y, f"{f}\n{'CPT' if fn == 'Captain' else 'FO'}",
                ha="center", va="center", fontsize=8, color="white",
                fontweight="bold", zorder=4)
        # Req/av annotation below
        ax.text(x, y - size - 0.07, f"{re:.0f} / {av:.1f}",
                ha="center", va="center", fontsize=7, color=MPL["navy"],
                zorder=4)

    ax.set_title("Fleet transition network (end-of-horizon)",
                 color=MPL["navy"], fontsize=12, fontweight="bold", pad=14)
    # Legend
    legend_patches = [
        mpatches.Patch(color=MPL["green"], label="Gap met"),
        mpatches.Patch(color=MPL["amber"], label="1 short"),
        mpatches.Patch(color=MPL["red"], label="2+ short"),
    ]
    ax.legend(handles=legend_patches, loc="lower center",
              bbox_to_anchor=(0.5, -0.05), ncol=3, frameon=False, fontsize=8)

    plt.tight_layout()
    return _fig_to_png(fig, dpi=220)


def _band_hex(gap: float) -> str:
    if gap < 1: return MPL["green"]
    if gap < 2: return MPL["amber"]
    return MPL["red"]


def _render_gantt(state, labels, horizon) -> io.BytesIO:
    """Gantt chart of all planned actions."""
    actions = sorted(state["actions"], key=lambda a: (a.start_month, a.action_type))
    if not actions:
        fig, ax = plt.subplots(figsize=(11, 2))
        ax.text(0.5, 0.5, "No actions planned",
                ha="center", va="center", fontsize=11, color=MPL["muted"])
        ax.axis("off")
        return _fig_to_png(fig)

    row_h = 0.32
    fig_h = max(3, min(12, 1.2 + row_h * len(actions)))
    fig, ax = plt.subplots(figsize=(11, fig_h))

    type_colors = {
        "Type Rating": MPL["blue"],
        "Command Upgrade": MPL["violet"],
        "Cadet Hire": MPL["green"],
        "Local Hire": MPL["green"],
        "Expat Hire": MPL["amber"],
        "Fleet Change": MPL["accent"],
        "Pilot Termination": MPL["red"],
    }

    pilot_by_id = {p.employee_id: p for p in state["pilots"]}
    y_labels = []
    for i, a in enumerate(actions):
        start = max(0, a.start_month)
        dur = max(0.5, a.duration if a.duration > 0 else 0.6)
        if start + dur > horizon:
            dur = horizon - start
        color = type_colors.get(a.action_type, MPL["muted"])
        ax.barh(i, dur, left=start, height=0.6,
                color=color, edgecolor="white", linewidth=1, alpha=0.92)

        # Label inside bar or to the right if too narrow
        label = _action_short_label(a, pilot_by_id)
        bar_center = start + dur / 2
        if dur >= 2:
            ax.text(bar_center, i, label, ha="center", va="center",
                    fontsize=7, color="white", fontweight="bold")
        else:
            ax.text(start + dur + 0.15, i, label, ha="left", va="center",
                    fontsize=7, color=MPL["navy"])

        # Y label
        mo = labels[start] if 0 <= start < len(labels) else f"M{start}"
        y_labels.append(f"{mo} · {a.action_type}")

    ax.set_yticks(range(len(actions)))
    ax.set_yticklabels(y_labels, fontsize=7, color=MPL["navy"])
    ax.invert_yaxis()
    ax.set_xlim(0, horizon)
    ax.set_xticks(range(0, horizon, max(1, horizon // 12)))
    ax.set_xticklabels(
        [labels[i] for i in range(0, horizon, max(1, horizon // 12))],
        rotation=45, ha="right", fontsize=7, color=MPL["muted"],
    )
    ax.grid(True, axis="x", linestyle=":", color=MPL["border"], linewidth=0.5)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(MPL["border"])

    ax.set_title("Timeline of planned actions",
                 color=MPL["navy"], fontsize=12, fontweight="bold", pad=12)

    # Legend
    seen = set()
    patches = []
    for a in actions:
        if a.action_type in seen: continue
        seen.add(a.action_type)
        patches.append(mpatches.Patch(
            color=type_colors.get(a.action_type, MPL["muted"]),
            label=a.action_type,
        ))
    ax.legend(handles=patches, loc="upper center",
              bbox_to_anchor=(0.5, -0.1), ncol=min(7, len(patches)),
              frameon=False, fontsize=7)

    plt.tight_layout()
    return _fig_to_png(fig, dpi=200)


def _action_short_label(a: PlannedAction, pilot_by_id: dict) -> str:
    if a.action_type == "Type Rating":
        names = []
        for t in a.trainee_ids[:2]:
            if t.startswith("SEAT:"): continue
            if t.startswith("TBD"):
                names.append("TBD")
            else:
                p = pilot_by_id.get(t)
                names.append(p.full_name.split()[-1] if p else t[:6])
        more = " +" + str(len(a.trainee_ids) - len(names)) if len(a.trainee_ids) > len(names) else ""
        return f"{a.from_fleet}→{a.to_fleet} {', '.join(names)}{more}"
    if a.action_type == "Command Upgrade":
        names = []
        for t in a.trainee_ids[:2]:
            if t.startswith("TBD"):
                names.append("TBD")
            else:
                p = pilot_by_id.get(t)
                names.append(p.full_name.split()[-1] if p else t[:6])
        return f"→{a.to_fleet} CPT {', '.join(names)}"
    if a.action_type in ("Cadet Hire", "Local Hire", "Expat Hire"):
        return f"{a.new_pilot_name or 'TBD'}"
    if a.action_type == "Pilot Termination":
        return f"{len(a.trainee_ids)} pilot(s)"
    if a.action_type == "Fleet Change":
        return a.note[:24] or a.from_fleet
    return a.action_type


def _render_pilot_journey_graph(action: PlannedAction, state) -> io.BytesIO:
    """
    Graph-theory style node diagram of a single action's cascade.
    Shows origin → training → arrival → (downstream slot openings).
    """
    pilot_by_id = {p.employee_id: p for p in state["pilots"]}
    fig, ax = plt.subplots(figsize=(10, 3.5))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 4)
    ax.axis("off")

    # Determine trainees
    trainees_real: list[tuple[str, str]] = []  # (display_name, origin_desc)
    for t in action.trainee_ids:
        if t.startswith("SEAT:"):
            pid = t[5:]
            p = pilot_by_id.get(pid)
            if p:
                trainees_real.append((f"{p.full_name} (seat support)",
                                       f"{p.fleet} {p.function}"))
        elif t.startswith("TBD"):
            trainees_real.append((t, "TBD origin"))
        else:
            p = pilot_by_id.get(t)
            if p:
                trainees_real.append((p.full_name, f"{p.fleet} {p.function}"))

    # Node columns
    def _node(x, y, text, color, width=1.9, height=0.6):
        box = FancyBboxPatch(
            (x - width / 2, y - height / 2), width, height,
            boxstyle="round,pad=0.04",
            facecolor=color, edgecolor="white", linewidth=2,
        )
        ax.add_patch(box)
        ax.text(x, y, text, ha="center", va="center",
                fontsize=7.5, color="white", fontweight="bold", wrap=True)

    def _arrow(x0, y0, x1, y1, label=""):
        arrow = FancyArrowPatch(
            (x0 + 0.95, y0), (x1 - 0.95, y1),
            arrowstyle="->,head_length=8,head_width=5",
            color=MPL["muted"], linewidth=1.4,
        )
        ax.add_patch(arrow)
        if label:
            ax.text((x0 + x1) / 2, y0 + 0.18, label,
                    ha="center", fontsize=6.5, color=MPL["muted"],
                    style="italic")

    if action.action_type == "Type Rating":
        _node(1.3, 2, f"{action.from_fleet}\n{action.from_function}",
              FLEET_HEX.get(action.from_fleet, MPL["muted"]))
        _node(4.5, 2, f"Type Rating\n{action.duration}mo · {action.mode}",
              MPL["blue"])
        _node(7.7, 2, f"{action.to_fleet}\n{action.to_function}",
              FLEET_HEX.get(action.to_fleet, MPL["muted"]))
        _arrow(1.3, 2, 4.5, 2)
        _arrow(4.5, 2, 7.7, 2, f"arrives +{action.duration}mo")
        # Trainee list below
        names = ", ".join(n for n, _ in trainees_real[:4])
        if len(trainees_real) > 4:
            names += f" +{len(trainees_real) - 4} more"
        if names:
            ax.text(4.5, 0.7, f"Trainees: {names}",
                    ha="center", fontsize=7.5, color=MPL["navy"])

    elif action.action_type == "Command Upgrade":
        _node(1.3, 2, f"{action.from_fleet or action.to_fleet}\nFirst Officer",
              FLEET_HEX.get(action.from_fleet or action.to_fleet, MPL["muted"]))
        _node(4.5, 2, f"Command Upgrade\n{action.duration}mo · {action.mode}",
              MPL["violet"])
        _node(7.7, 2, f"{action.to_fleet}\nCaptain",
              FLEET_HEX.get(action.to_fleet, MPL["muted"]))
        _arrow(1.3, 2, 4.5, 2)
        _arrow(4.5, 2, 7.7, 2, f"+{action.duration}mo")
        names = ", ".join(n for n, _ in trainees_real[:4])
        if names:
            ax.text(4.5, 0.7, f"Candidates: {names}",
                    ha="center", fontsize=7.5, color=MPL["navy"])

    elif action.action_type in ("Cadet Hire", "Local Hire", "Expat Hire"):
        _node(1.3, 2, "New hire", MPL["green"] if "Local" in action.action_type
              or "Cadet" in action.action_type else MPL["amber"])
        _node(4.5, 2, f"Training\n{action.duration}mo", MPL["blue"])
        _node(7.7, 2, f"{action.to_fleet}\n{action.to_function}",
              FLEET_HEX.get(action.to_fleet, MPL["muted"]))
        _arrow(1.3, 2, 4.5, 2)
        _arrow(4.5, 2, 7.7, 2)
        ax.text(4.5, 0.7, f"Name: {action.new_pilot_name or 'TBD'}",
                ha="center", fontsize=7.5, color=MPL["navy"])

    elif action.action_type == "Pilot Termination":
        _node(3, 2, f"{len(action.trainee_ids)} pilot(s)",
              MPL["muted"])
        _node(7, 2, "Departed roster", MPL["red"])
        _arrow(3, 2, 7, 2)
        names = []
        for t in action.trainee_ids[:4]:
            p = pilot_by_id.get(t)
            names.append(p.full_name if p else t)
        more = f" +{len(action.trainee_ids) - 4} more" if len(action.trainee_ids) > 4 else ""
        ax.text(5, 0.7, ", ".join(names) + more,
                ha="center", fontsize=7.5, color=MPL["navy"], wrap=True)

    elif action.action_type == "Fleet Change":
        _node(3, 2, f"{action.from_fleet}", FLEET_HEX.get(action.from_fleet, MPL["muted"]))
        _node(7, 2, "Fleet change", MPL["accent"])
        _arrow(3, 2, 7, 2)
        ax.text(5, 0.7, action.note or "—",
                ha="center", fontsize=7.5, color=MPL["navy"])

    cost = getattr(action, "cost", 0) or 0
    cur = getattr(action, "cost_currency", "USD") or "USD"
    if cost > 0:
        ax.text(9.5, 3.6, f"{cur} {cost:,.0f}",
                ha="right", fontsize=8, color=MPL["accent"], fontweight="bold")

    plt.tight_layout()
    return _fig_to_png(fig, dpi=200)


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------
def _table_style_base():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), C_SURFACE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), C_NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, C_BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_SURFACE, C_BG]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ])


def _build_fleet_summary_table(state, req, avail, labels, styles) -> Table:
    rows = [[
        _P("Fleet", "CellHead"),
        _P("Aircraft", "CellHead"),
        _P("CPT req.", "CellHead"),
        _P("CPT avail.", "CellHead"),
        _P("FO req.", "CellHead"),
        _P("FO avail.", "CellHead"),
        _P("Local %", "CellHead"),
        _P("Worst gap", "CellHead"),
    ]]
    loc = localisation_summary(state["pilots"])
    ac_counts = resolve_aircraft_counts(
        state["initial_aircraft"], state["fleet_changes"], state["horizon"])

    style_extra = []
    for row_i, f in enumerate(FLEETS, start=1):
        cap_req = req[f]["Captain"][0]
        fo_req = req[f]["First Officer"][0]
        cap_av = avail[f]["Captain"][0]
        fo_av = avail[f]["First Officer"][0]
        worst_gap = max(
            max(req[f]["Captain"][i] - avail[f]["Captain"][i] for i in range(len(labels))),
            max(req[f]["First Officer"][i] - avail[f]["First Officer"][i] for i in range(len(labels))),
        )
        worst_gap = max(0, worst_gap)
        band = gap_band(worst_gap)
        loc_f = loc["by_fleet"][f]
        pct = (loc_f["local"] / loc_f["total"] * 100) if loc_f["total"] else 0

        rows.append([
            _P(f"<b>{f}</b>", "CellLeft"),
            _P(str(ac_counts[f][0]), "CellCenter"),
            _P(str(cap_req), "CellCenter"),
            _P(f"{cap_av:.1f}", "CellCenter"),
            _P(str(fo_req), "CellCenter"),
            _P(f"{fo_av:.1f}", "CellCenter"),
            _P(f"{pct:.0f}%", "CellCenter"),
            _P(f"{worst_gap:.1f}", "CellCenter"),
        ])
        style_extra.append(
            ("TEXTCOLOR", (7, row_i), (7, row_i), _color_for_band(band))
        )
        style_extra.append(
            ("FONTNAME", (7, row_i), (7, row_i), "Helvetica-Bold")
        )

    tbl = Table(rows, colWidths=[2.2 * cm, 1.8 * cm, 1.8 * cm, 1.9 * cm,
                                  1.8 * cm, 1.9 * cm, 1.9 * cm, 2.0 * cm])
    style = _table_style_base()
    for ext in style_extra:
        style.add(*ext)
    tbl.setStyle(style)
    return tbl


def _build_action_table(state, labels, styles, detailed=True) -> Table:
    pilot_by_id = {p.employee_id: p for p in state["pilots"]}

    if detailed:
        header = ["Month", "Type", "From → To", "Mode", "Duration",
                  "Instructor", "Trainees", "Cost", "Note"]
        col_widths = [1.8 * cm, 2.0 * cm, 3.6 * cm, 1.5 * cm, 1.5 * cm,
                      2.0 * cm, 4.2 * cm, 2.2 * cm, 4.2 * cm]
    else:
        header = ["Month", "Type", "From → To", "Duration", "Cost"]
        col_widths = [2.2 * cm, 2.6 * cm, 5.0 * cm, 2.0 * cm, 2.6 * cm]

    rows = [[_P(h, "CellHead") for h in header]]
    style_extra = []

    for row_i, a in enumerate(sorted(state["actions"],
                                     key=lambda x: x.start_month), start=1):
        mo = labels[a.start_month] if 0 <= a.start_month < len(labels) else f"M{a.start_month}"

        if a.action_type == "Pilot Termination":
            # Termination: show departing names in place of from → to
            names = []
            for t in a.trainee_ids[:3]:
                p = pilot_by_id.get(t)
                names.append(p.full_name if p else t)
            from_to = "Departing: " + ", ".join(names)
            if len(a.trainee_ids) > 3:
                from_to += f" +{len(a.trainee_ids) - 3} more"
        elif a.action_type == "Fleet Change":
            from_to = a.from_fleet
        elif a.action_type in ("Cadet Hire", "Local Hire", "Expat Hire"):
            from_to = f"{a.new_pilot_name or 'TBD'} → {a.to_fleet} {a.to_function}"
        else:
            from_part = f"{a.from_fleet} {a.from_function}".strip() or "—"
            to_part = f"{a.to_fleet} {a.to_function}".strip() or "—"
            from_to = f"{from_part} → {to_part}"

        cost_val = getattr(a, "cost", 0) or 0
        cost_cur = getattr(a, "cost_currency", "USD") or "USD"
        cost_disp = f"{cost_cur} {cost_val:,.0f}" if cost_val > 0 else "—"

        dur_str = f"{a.duration}mo" if a.duration else "—"

        if detailed:
            instr = a.instructor_id or "—"
            trainee_names = []
            for t in a.trainee_ids:
                if t.startswith("SEAT:"):
                    pid = t[5:]
                    p = pilot_by_id.get(pid)
                    trainee_names.append(f"{p.full_name} (SS)" if p else f"{pid} (SS)")
                elif t.startswith("TBD"):
                    trainee_names.append(t)
                else:
                    p = pilot_by_id.get(t)
                    trainee_names.append(p.full_name if p else t)
            trainees_str = ", ".join(trainee_names) if trainee_names else "—"
            note_str = a.note or "—"
            rows.append([
                _P(mo, "CellCenter"),
                _P(a.action_type, "CellLeft"),
                _P(from_to, "CellLeft"),
                _P(a.mode or "—", "CellCenter"),
                _P(dur_str, "CellCenter"),
                _P(instr, "CellLeft"),
                _P(trainees_str, "CellLeft"),
                _P(cost_disp, "CellRight"),
                _P(note_str, "CellLeft"),
            ])
        else:
            rows.append([
                _P(mo, "CellCenter"),
                _P(a.action_type, "CellLeft"),
                _P(from_to, "CellLeft"),
                _P(dur_str, "CellCenter"),
                _P(cost_disp, "CellRight"),
            ])

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    style = _table_style_base()
    tbl.setStyle(style)
    return tbl


def _build_pilot_roster_table(state, styles) -> Table:
    header = ["Employee ID", "Name", "Fleet", "Function", "Nationality",
              "Management", "Designations", "Status"]
    col_widths = [2.5 * cm, 4.5 * cm, 1.8 * cm, 2.4 * cm, 2.0 * cm,
                  2.0 * cm, 2.8 * cm, 2.0 * cm]
    rows = [[_P(h, "CellHead") for h in header]]
    for p in sorted(state["pilots"], key=lambda x: (x.fleet, x.function, x.full_name)):
        rows.append([
            _P(p.employee_id, "CellLeft"),
            _P(p.full_name, "CellLeft"),
            _P(p.fleet, "CellCenter"),
            _P(p.function, "CellLeft"),
            _P(p.nationality, "CellCenter"),
            _P("Yes" if p.management else "No", "CellCenter"),
            _P(", ".join(p.designations) if p.designations else "—", "CellLeft"),
            _P(p.status, "CellCenter"),
        ])
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(_table_style_base())
    return tbl


def _build_monthly_grid_table(req, avail, labels, styles) -> Table:
    # Transposed: rows = fleet × function, cols = months
    # Limit to ~12 months per table page to keep cells readable
    max_cols = 12
    slices = []
    for start in range(0, len(labels), max_cols):
        slices.append((start, min(start + max_cols, len(labels))))

    tables = []
    for start, end in slices:
        sub_labels = labels[start:end]
        header = ["Fleet / Func"] + sub_labels
        col_widths = [3.0 * cm] + [(23 - 3.0) / len(sub_labels) * cm] * len(sub_labels)

        rows = [[_P(h, "CellHead") for h in header]]
        style_extra = []

        row_i = 1
        for f in FLEETS:
            for fn in FUNCTIONS:
                row = [_P(f"<b>{f} {fn[:3]}</b>", "CellLeft")]
                for i in range(start, end):
                    gap = max(0, req[f][fn][i] - avail[f][fn][i])
                    band = gap_band(gap)
                    txt = f"{req[f][fn][i]} / {avail[f][fn][i]:.1f}"
                    row.append(_P(txt, "CellCenter"))
                    if band != "green":
                        color = _color_for_band(band)
                        style_extra.append(
                            ("TEXTCOLOR", (1 + (i - start), row_i),
                             (1 + (i - start), row_i), color)
                        )
                        style_extra.append(
                            ("FONTNAME", (1 + (i - start), row_i),
                             (1 + (i - start), row_i), "Helvetica-Bold")
                        )
                rows.append(row)
                row_i += 1

        tbl = Table(rows, colWidths=col_widths, repeatRows=1)
        style = _table_style_base()
        for ext in style_extra:
            style.add(*ext)
        tbl.setStyle(style)
        tables.append(tbl)

    return tables


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def build_pdf(state: dict, mode: str = "executive") -> bytes:
    """
    Build a PDF from the state dict. mode = 'executive' or 'comprehensive'.
    """
    styles = _styles()
    labels = month_labels(state["start_year"], state["start_month"], state["horizon"])
    ac_counts = resolve_aircraft_counts(
        state["initial_aircraft"], state["fleet_changes"], state["horizon"])
    req = fleet_requirement(ac_counts)
    avail = compute_availability(state["pilots"], state["actions"], state["horizon"])

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=1.6 * cm, bottomMargin=1.4 * cm,
        title="IASL Crew Plan",
    )
    doc.doc_title = "Crew Planning Report"

    story = []

    # ---- Cover ----
    story.append(Spacer(1, 3.5 * cm))
    story.append(Paragraph("IASL Crew Planning Report", styles["Cover"]))
    story.append(Paragraph(
        f"Horizon: {labels[0]} → {labels[-1]} &nbsp;·&nbsp; {state['horizon']} months",
        styles["CoverSub"]))
    story.append(Paragraph(
        f"Mode: {mode.title()} &nbsp;·&nbsp; Generated: {date.today().strftime('%d %b %Y')}",
        styles["CoverMeta"]))
    story.append(Spacer(1, 1.5 * cm))

    # Cover summary stats
    loc = localisation_summary(state["pilots"])
    total_actions = len(state["actions"])
    total_cost_lines = _total_costs(state)

    summary_rows = [
        ["Total pilots", str(len(state["pilots"]))],
        ["Total aircraft", str(sum(state["initial_aircraft"].values()))],
        ["Planning horizon", f"{state['horizon']} months"],
        ["Actions planned", str(total_actions)],
        ["Overall localisation", f"{loc['local_pct']:.0f}%"],
    ]
    for cur, v in total_cost_lines:
        summary_rows.append([f"Total cost ({cur})", f"{cur} {v:,.0f}"])

    cov_tbl = Table(
        [[_P(f"<b>{r[0]}</b>", "CellLeft"), _P(r[1], "CellRight")]
         for r in summary_rows],
        colWidths=[6 * cm, 6 * cm],
    )
    cov_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.3, C_BORDER),
        ("BACKGROUND", (0, 0), (-1, -1), C_SURFACE),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(KeepTogether([cov_tbl]))
    story.append(PageBreak())

    # ---- Section 1: Executive summary ----
    story.append(Paragraph("1. Executive summary", styles["H1"]))
    story.append(Paragraph(
        f"This report summarises the crew plan for Island Aviation Services "
        f"Limited covering the {state['horizon']}-month horizon from "
        f"<b>{labels[0]}</b> to <b>{labels[-1]}</b>. The plan spans "
        f"<b>{len(state['pilots'])} pilots</b> across four fleets (A330, A320, "
        f"ATR72, DHC-8), with <b>{total_actions} scheduled actions</b> "
        f"including training, command upgrades, hires, and transitions.",
        styles["Body"]))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("Fleet-level snapshot (month 1)", styles["H2"]))
    story.append(_build_fleet_summary_table(state, req, avail, labels, styles))
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Requirement gap heatmap", styles["H2"]))
    heatmap_png = _render_gap_heatmap(req, avail, labels)
    story.append(Image(heatmap_png, width=24 * cm, height=7.5 * cm))
    story.append(PageBreak())

    # ---- Section 2: Fleet transitions (network graph) ----
    story.append(Paragraph("2. Fleet transition network", styles["H1"]))
    story.append(Paragraph(
        "The network graph below shows how pilots move between fleets and "
        "functions under this plan. Edge thickness indicates the number of "
        "pilots transitioning; node colour indicates gap severity at the end "
        "of the horizon. Hover the full IASL app to drill into any edge.",
        styles["Body"]))
    story.append(Spacer(1, 0.2 * cm))
    net_png = _render_fleet_network_graph(state, labels)
    story.append(Image(net_png, width=23 * cm, height=14 * cm))
    story.append(PageBreak())

    # ---- Section 3: Timeline Gantt ----
    story.append(Paragraph("3. Action timeline", styles["H1"]))
    story.append(Paragraph(
        "Each bar represents one planned action. Colour indicates action type. "
        "Actions with zero duration (e.g., terminations, fleet changes) appear "
        "as minimal bars at their start month.",
        styles["Body"]))
    gantt_png = _render_gantt(state, labels, state["horizon"])
    story.append(Image(gantt_png, width=24 * cm, height=min(14, 1.2 + 0.3 * len(state["actions"])) * cm))
    story.append(PageBreak())

    # ---- Section 4: Requirement vs availability ----
    story.append(Paragraph("4. Requirement vs availability by fleet", styles["H1"]))
    req_av_png = _render_req_vs_avail(req, avail, labels)
    story.append(Image(req_av_png, width=24 * cm, height=12 * cm))
    story.append(PageBreak())

    # ---- Section 5: Localisation & cost ----
    story.append(Paragraph("5. Localisation and cost trajectory", styles["H1"]))
    loc_png = _render_localisation_curve(state, labels, state["horizon"])
    story.append(Image(loc_png, width=24 * cm, height=6.5 * cm))
    story.append(Spacer(1, 0.3 * cm))

    cost_png = _render_cost_curves(state, labels, state["horizon"])
    if cost_png is not None:
        story.append(Image(cost_png, width=24 * cm, height=6.5 * cm))
    else:
        story.append(Paragraph(
            "<i>No action costs have been entered; cost curves are not shown.</i>",
            styles["Caption"]))
    story.append(PageBreak())

    # ---- Section 6: Action list ----
    story.append(Paragraph("6. Scheduled actions", styles["H1"]))
    story.append(Paragraph(
        "Full list of all scheduled actions with costs, instructors, and "
        "trainee allocations. All text wraps within cells.",
        styles["Body"]))
    story.append(Spacer(1, 0.2 * cm))
    action_tbl = _build_action_table(state, labels, styles,
                                     detailed=(mode == "comprehensive"))
    story.append(action_tbl)
    story.append(PageBreak())

    # ---- Section 7: Per-action pilot journey graphs (comprehensive only) ----
    if mode == "comprehensive" and state["actions"]:
        story.append(Paragraph("7. Pilot journey graphs", styles["H1"]))
        story.append(Paragraph(
            "One node diagram per scheduled action showing origin → "
            "intermediate training → destination, along with the pilots "
            "involved.", styles["Body"]))
        story.append(Spacer(1, 0.2 * cm))

        pilot_by_id = {p.employee_id: p for p in state["pilots"]}
        for idx, a in enumerate(sorted(state["actions"],
                                       key=lambda x: x.start_month)):
            mo = labels[a.start_month] if 0 <= a.start_month < len(labels) else f"M{a.start_month}"
            header_text = f"<b>{idx + 1}. {mo} — {a.action_type}</b>"
            story.append(CondPageBreak(8 * cm))
            story.append(Paragraph(header_text, styles["H3"]))
            journey_png = _render_pilot_journey_graph(a, state)
            story.append(Image(journey_png, width=22 * cm, height=7.5 * cm))
            if a.note:
                story.append(Paragraph(f"<i>Note: {a.note}</i>", styles["Caption"]))
            story.append(Spacer(1, 0.2 * cm))
        story.append(PageBreak())

    # ---- Section 8: Pilot roster ----
    story.append(Paragraph(
        "8. Pilot roster" if mode == "comprehensive" else "7. Pilot roster",
        styles["H1"]))
    story.append(_build_pilot_roster_table(state, styles))
    story.append(PageBreak())

    # ---- Section 9: Month-by-month requirement grid ----
    story.append(Paragraph(
        "9. Month-by-month requirement vs availability"
        if mode == "comprehensive"
        else "8. Month-by-month requirement vs availability",
        styles["H1"]))
    story.append(Paragraph(
        "Format: required / available. Red = 2+ short, amber = 1 short, "
        "black = met. Tables split at 12-month chunks for readability.",
        styles["Body"]))
    story.append(Spacer(1, 0.2 * cm))
    grid_tables = _build_monthly_grid_table(req, avail, labels, styles)
    for i, tbl in enumerate(grid_tables):
        story.append(tbl)
        if i < len(grid_tables) - 1:
            story.append(Spacer(1, 0.4 * cm))

    # ---- Build ----
    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


def _total_costs(state) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for a in state["actions"]:
        cost = getattr(a, "cost", 0) or 0
        cur = getattr(a, "cost_currency", "USD") or "USD"
        if cost > 0:
            totals[cur] = totals.get(cur, 0) + cost
    return sorted(totals.items())
