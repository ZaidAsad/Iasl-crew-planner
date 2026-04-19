"""
IASL Crew Planning Portal — PDF export module.

Generates a landscape multi-page PDF using ReportLab. Consumes the same
cascade engine the UI uses, and renders cascade diagrams as static PNGs
via Plotly + Kaleido so the PDF shows exactly what the user saw on screen.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
    Table, TableStyle, PageBreak, Image, KeepTogether,
)
from reportlab.pdfgen import canvas as _canvas

import plotly.graph_objects as go

from cascade_engine import (
    Pilot, PlannedAction, FleetChange,
    FLEETS, CREW_SETS_PER_AIRCRAFT,
    month_labels, resolve_aircraft_counts, fleet_requirement,
    compute_availability, compute_gaps, gap_band,
    build_cascade_graph, localisation_summary,
)

# ---------------------------------------------------------------------------
# PDF colour palette (print-friendly, slightly different from UI dark theme)
# ---------------------------------------------------------------------------
PDF_NAVY    = colors.HexColor("#0F2944")
PDF_TEAL    = colors.HexColor("#00857A")
PDF_LIGHT   = colors.HexColor("#F5F7FA")
PDF_BORDER  = colors.HexColor("#D5DCE5")
PDF_MUTED   = colors.HexColor("#6B7A8F")
PDF_GREEN   = colors.HexColor("#16A34A")
PDF_AMBER   = colors.HexColor("#D97706")
PDF_RED     = colors.HexColor("#DC2626")
PDF_TEXT    = colors.HexColor("#1F2937")

BAND_COLORS = {"green": PDF_GREEN, "amber": PDF_AMBER, "red": PDF_RED}


# ---------------------------------------------------------------------------
# Page template with header + footer
# ---------------------------------------------------------------------------
class IASLDocTemplate(BaseDocTemplate):
    """Landscape A4 with running header and page numbers."""

    def __init__(self, filename, period_label: str, **kw):
        super().__init__(filename, pagesize=landscape(A4), **kw)
        self.period_label = period_label
        self.allowSplitting = 1
        frame = Frame(
            15 * mm, 15 * mm,
            self.pagesize[0] - 30 * mm,
            self.pagesize[1] - 30 * mm,
            id="body",
        )
        self.addPageTemplates([
            PageTemplate(id="main", frames=[frame], onPage=self._draw_chrome),
        ])

    def _draw_chrome(self, c: _canvas.Canvas, doc):
        w, h = self.pagesize
        # Header band
        c.saveState()
        c.setFillColor(PDF_NAVY)
        c.rect(0, h - 12 * mm, w, 12 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(15 * mm, h - 8 * mm, "IASL  ·  Crew Planning Portal")
        c.setFont("Helvetica", 9)
        c.drawRightString(w - 15 * mm, h - 8 * mm, self.period_label)

        # Footer
        c.setFillColor(PDF_MUTED)
        c.setFont("Helvetica", 8)
        c.drawString(15 * mm, 8 * mm,
                     f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        c.drawRightString(w - 15 * mm, 8 * mm, f"Page {doc.page}")
        c.setStrokeColor(PDF_BORDER)
        c.setLineWidth(0.4)
        c.line(15 * mm, 11 * mm, w - 15 * mm, 11 * mm)
        c.restoreState()


# ---------------------------------------------------------------------------
# Paragraph styles
# ---------------------------------------------------------------------------
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    s = {
        "H1": ParagraphStyle(
            "H1", parent=base["Heading1"],
            fontName="Helvetica-Bold", fontSize=22, leading=26,
            textColor=PDF_NAVY, spaceAfter=8,
        ),
        "H2": ParagraphStyle(
            "H2", parent=base["Heading2"],
            fontName="Helvetica-Bold", fontSize=14, leading=18,
            textColor=PDF_NAVY, spaceBefore=10, spaceAfter=6,
        ),
        "H3": ParagraphStyle(
            "H3", parent=base["Heading3"],
            fontName="Helvetica-Bold", fontSize=11, leading=14,
            textColor=PDF_TEAL, spaceBefore=6, spaceAfter=4,
        ),
        "Body": ParagraphStyle(
            "Body", parent=base["Normal"],
            fontName="Helvetica", fontSize=9.5, leading=13,
            textColor=PDF_TEXT,
        ),
        "Muted": ParagraphStyle(
            "Muted", parent=base["Normal"],
            fontName="Helvetica", fontSize=8.5, leading=11,
            textColor=PDF_MUTED,
        ),
        "CoverTitle": ParagraphStyle(
            "CoverTitle", parent=base["Heading1"],
            fontName="Helvetica-Bold", fontSize=34, leading=40,
            textColor=PDF_NAVY,
        ),
        "CoverSub": ParagraphStyle(
            "CoverSub", parent=base["Normal"],
            fontName="Helvetica", fontSize=14, leading=20,
            textColor=PDF_MUTED,
        ),
    }
    return s


# ---------------------------------------------------------------------------
# Cascade graph → PNG via Plotly + Kaleido
# ---------------------------------------------------------------------------
def _render_cascade_png(graph: dict[str, Any]) -> bytes | None:
    """
    Render a cascade graph as a static PNG using a Plotly network-style layout.
    Returns PNG bytes or None if Kaleido is unavailable.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        return None

    # Assign x by month (or fallback to depth via BFS from root), y by row.
    # Depth pass
    depth: dict[str, int] = {}
    children: dict[str, list[str]] = {}
    for e in edges:
        children.setdefault(e["source"], []).append(e["target"])
    roots = [n["id"] for n in nodes if n["id"] == "root"] or [nodes[0]["id"]]
    queue = [(r, 0) for r in roots]
    while queue:
        nid, d = queue.pop(0)
        if nid in depth and depth[nid] <= d:
            continue
        depth[nid] = d
        for c in children.get(nid, []):
            queue.append((c, d + 1))
    for n in nodes:
        depth.setdefault(n["id"], 0)

    # Group by depth for vertical stacking
    by_depth: dict[int, list[str]] = {}
    for nid, d in depth.items():
        by_depth.setdefault(d, []).append(nid)

    pos: dict[str, tuple[float, float]] = {}
    max_d = max(by_depth.keys()) if by_depth else 0
    for d, ids in by_depth.items():
        n = len(ids)
        for i, nid in enumerate(ids):
            x = d * 2.4
            y = (n - 1) / 2.0 - i  # centre vertically
            pos[nid] = (x, y)

    kind_color = {
        "trigger":  "#0F2944",
        "slot":     "#D97706",
        "training": "#3B82F6",
        "arrival":  "#16A34A",
        "note":     "#DC2626",
    }

    # Edge traces
    edge_x, edge_y = [], []
    for e in edges:
        if e["source"] not in pos or e["target"] not in pos:
            continue
        x0, y0 = pos[e["source"]]
        x1, y1 = pos[e["target"]]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=edge_x, y=edge_y, mode="lines",
        line=dict(color="#94A3B8", width=1.4),
        hoverinfo="none", showlegend=False,
    ))

    # Node traces — one scatter per kind for legend clarity
    kinds_seen: set[str] = set()
    for kind in ("trigger", "slot", "training", "arrival", "note"):
        xs, ys, texts = [], [], []
        for n in nodes:
            if n["kind"] != kind:
                continue
            if n["id"] not in pos:
                continue
            x, y = pos[n["id"]]
            xs.append(x)
            ys.append(y)
            texts.append(n["label"].replace("\n", "<br>"))
        if not xs:
            continue
        kinds_seen.add(kind)
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="markers+text",
            marker=dict(size=42, color=kind_color[kind],
                        line=dict(color="white", width=2)),
            text=texts, textposition="middle right",
            textfont=dict(size=10, color="#1F2937"),
            name=kind.capitalize(),
            hoverinfo="text",
        ))

    fig.update_layout(
        paper_bgcolor="white",
        plot_bgcolor="white",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15,
                    xanchor="center", x=0.5, font=dict(size=10)),
        margin=dict(l=20, r=200, t=10, b=40),
        xaxis=dict(visible=False, range=[-0.5, max_d * 2.4 + 3]),
        yaxis=dict(visible=False),
        height=320,
        width=1000,
    )

    try:
        return fig.to_image(format="png", scale=2)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Small table helper
# ---------------------------------------------------------------------------
def _simple_table(data, col_widths=None, header_bg=PDF_NAVY, zebra=True):
    t = Table(data, colWidths=col_widths, repeatRows=1)
    ts = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 8.5),
        ("ALIGN",      (0, 0), (-1, 0), "LEFT"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING",    (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("TOPPADDING",    (0, 1), (-1, -1), 4),
        ("GRID",          (0, 0), (-1, -1), 0.25, PDF_BORDER),
        ("TEXTCOLOR",     (0, 1), (-1, -1), PDF_TEXT),
    ])
    if zebra:
        for r in range(1, len(data)):
            if r % 2 == 0:
                ts.add("BACKGROUND", (0, r), (-1, r), PDF_LIGHT)
    t.setStyle(ts)
    return t


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------
def build_pdf(state: dict[str, Any]) -> bytes:
    """
    state keys: start_year, start_month, horizon, initial_aircraft,
                pilots, fleet_changes, actions
    Returns bytes of the generated PDF.
    """
    pilots: list[Pilot] = state["pilots"]
    actions: list[PlannedAction] = state["actions"]
    fleet_changes: list[FleetChange] = state["fleet_changes"]
    horizon: int = state["horizon"]
    start_year: int = state["start_year"]
    start_month: int = state["start_month"]
    initial_aircraft: dict[str, int] = state["initial_aircraft"]

    labels = month_labels(start_year, start_month, horizon)
    period_label = f"{labels[0]} → {labels[-1]}" if labels else ""

    ac_counts = resolve_aircraft_counts(initial_aircraft, fleet_changes, horizon)
    req = fleet_requirement(ac_counts)
    avail = compute_availability(pilots, actions, horizon)
    gaps = compute_gaps(req, avail)

    loc = localisation_summary(pilots)

    styles = _styles()
    buf = BytesIO()
    doc = IASLDocTemplate(buf, period_label=period_label)
    story: list = []

    # ------------------------- COVER PAGE -------------------------
    story.append(Spacer(1, 40 * mm))
    story.append(Paragraph("Crew Planning Portal", styles["CoverTitle"]))
    story.append(Paragraph("Island Aviation Services Limited", styles["CoverSub"]))
    story.append(Spacer(1, 20 * mm))

    cover_tbl = Table([
        ["Planning period", period_label],
        ["Horizon",         f"{horizon} months"],
        ["Total pilots",    str(len(pilots))],
        ["Total aircraft",  str(sum(initial_aircraft.values()))],
        ["Generated",       datetime.now().strftime("%Y-%m-%d %H:%M")],
    ], colWidths=[45 * mm, 100 * mm])
    cover_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("TEXTCOLOR", (0, 0), (0, -1), PDF_MUTED),
        ("TEXTCOLOR", (1, 0), (1, -1), PDF_TEXT),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, PDF_BORDER),
    ]))
    story.append(cover_tbl)
    story.append(PageBreak())

    # ------------------------- EXECUTIVE SUMMARY -------------------------
    story.append(Paragraph("Executive Summary", styles["H1"]))
    story.append(Spacer(1, 4))

    # Top-line KPI row
    kpi = [
        ["Pilots",    "Aircraft", "Localisation",     "Planned actions"],
        [str(len(pilots)),
         str(sum(initial_aircraft.values())),
         f"{loc['local_pct']:.0f}%",
         str(len(actions))],
    ]
    kpi_tbl = Table(kpi, colWidths=[60 * mm] * 4)
    kpi_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PDF_LIGHT),
        ("TEXTCOLOR",  (0, 0), (-1, 0), PDF_MUTED),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica"),
        ("FONTSIZE",   (0, 0), (-1, 0), 8),
        ("ALIGN",      (0, 0), (-1, -1), "LEFT"),
        ("FONTNAME",   (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 1), (-1, 1), 22),
        ("TEXTCOLOR",  (0, 1), (-1, 1), PDF_NAVY),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("TOPPADDING",    (0, 0), (-1, 0), 8),
        ("BOX", (0, 0), (-1, -1), 0.3, PDF_BORDER),
    ]))
    story.append(kpi_tbl)

    # Fleet mix
    story.append(Paragraph("Fleet composition (month 1)", styles["H2"]))
    fleet_rows = [["Fleet", "Aircraft", "Crew sets / AC", "Captain req.", "FO req.",
                   "Captain avail.", "FO avail.", "Status"]]
    for f in FLEETS:
        cap_req = req[f]["Captain"][0]
        fo_req  = req[f]["First Officer"][0]
        cap_av  = avail[f]["Captain"][0]
        fo_av   = avail[f]["First Officer"][0]
        worst = max(gaps[f]["Captain"][0], gaps[f]["First Officer"][0])
        band = gap_band(worst)
        fleet_rows.append([
            f, str(ac_counts[f][0]), str(CREW_SETS_PER_AIRCRAFT[f]),
            str(cap_req), str(fo_req),
            f"{cap_av:.1f}", f"{fo_av:.1f}",
            band.upper(),
        ])
    tbl = _simple_table(fleet_rows,
                        col_widths=[22 * mm, 20 * mm, 28 * mm, 25 * mm,
                                    22 * mm, 28 * mm, 22 * mm, 20 * mm])
    # Colour the status cells
    ts_extra = TableStyle([])
    for ri in range(1, len(fleet_rows)):
        band = fleet_rows[ri][-1].lower()
        ts_extra.add("TEXTCOLOR", (-1, ri), (-1, ri), BAND_COLORS.get(band, PDF_TEXT))
        ts_extra.add("FONTNAME", (-1, ri), (-1, ri), "Helvetica-Bold")
    tbl.setStyle(ts_extra)
    story.append(tbl)

    # Key actions summary
    story.append(Paragraph("Key actions", styles["H2"]))
    if actions:
        action_rows = [["Month", "Type", "From → To", "Duration", "Mode", "Note"]]
        for a in sorted(actions, key=lambda x: x.start_month):
            mo = labels[a.start_month] if 0 <= a.start_month < len(labels) else f"M{a.start_month}"
            route = ""
            if a.action_type == "Type Rating":
                route = f"{a.from_fleet} {a.from_function} → {a.to_fleet} {a.to_function}"
            elif a.action_type == "Command Upgrade":
                route = f"{a.from_fleet} FO → {a.to_fleet} CPT"
            elif a.action_type in ("Cadet Hire", "Expat Hire", "Local Hire"):
                route = f"→ {a.to_fleet} {a.to_function}"
            elif a.action_type == "Fleet Change":
                route = f"{a.from_fleet or ''}"
            action_rows.append([
                mo, a.action_type, route,
                f"{a.duration}mo" if a.duration else "—",
                a.mode or "—",
                (a.note or "")[:60],
            ])
        story.append(_simple_table(
            action_rows,
            col_widths=[22 * mm, 34 * mm, 62 * mm, 20 * mm, 22 * mm, 60 * mm],
        ))
    else:
        story.append(Paragraph("No actions planned yet.", styles["Muted"]))

    story.append(PageBreak())

    # ------------------------- PER-FLEET BREAKDOWN -------------------------
    story.append(Paragraph("Per-fleet breakdown", styles["H1"]))
    for f in FLEETS:
        story.append(Paragraph(f, styles["H2"]))

        # Fleet snapshot
        fleet_pilots = [p for p in pilots if p.fleet == f]
        cap_count = sum(1 for p in fleet_pilots if p.function == "Captain")
        fo_count  = sum(1 for p in fleet_pilots if p.function == "First Officer")
        mgmt = sum(1 for p in fleet_pilots if p.management)
        local_n = sum(1 for p in fleet_pilots if p.nationality == "Local")
        expat_n = sum(1 for p in fleet_pilots if p.nationality == "Expat")

        snap = [
            ["Captains", "First Officers", "Management", "Local", "Expat"],
            [str(cap_count), str(fo_count), str(mgmt), str(local_n), str(expat_n)],
        ]
        snap_tbl = Table(snap, colWidths=[40 * mm] * 5)
        snap_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PDF_LIGHT),
            ("TEXTCOLOR",  (0, 0), (-1, 0), PDF_MUTED),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica"),
            ("FONTSIZE",   (0, 0), (-1, 0), 8),
            ("FONTNAME",   (0, 1), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 1), (-1, 1), 14),
            ("TEXTCOLOR",  (0, 1), (-1, 1), PDF_NAVY),
            ("BOX", (0, 0), (-1, -1), 0.3, PDF_BORDER),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ]))
        story.append(snap_tbl)

        # Fleet actions chronologically
        fa = [a for a in actions if f in (a.from_fleet, a.to_fleet)]
        if fa:
            rows = [["Month", "Type", "Detail", "Duration"]]
            for a in sorted(fa, key=lambda x: x.start_month):
                mo = labels[a.start_month] if 0 <= a.start_month < len(labels) else f"M{a.start_month}"
                detail = ""
                if a.action_type == "Type Rating":
                    detail = f"{a.from_fleet} {a.from_function} → {a.to_fleet} {a.to_function}"
                elif a.action_type == "Command Upgrade":
                    detail = f"{a.from_fleet} FO → {a.to_fleet} CPT"
                elif a.action_type in ("Cadet Hire", "Expat Hire", "Local Hire"):
                    detail = f"{a.new_pilot_name or 'TBD'} → {a.to_fleet} {a.to_function}"
                rows.append([mo, a.action_type, detail, f"{a.duration}mo"])
            story.append(Spacer(1, 4))
            story.append(_simple_table(
                rows,
                col_widths=[22 * mm, 34 * mm, 150 * mm, 24 * mm],
            ))
        else:
            story.append(Paragraph("No planned actions touch this fleet.", styles["Muted"]))
        story.append(Spacer(1, 8))
    story.append(PageBreak())

    # ------------------------- MONTHLY TIMELINE TABLE -------------------------
    story.append(Paragraph("Monthly requirement vs availability", styles["H1"]))
    story.append(Paragraph(
        "Values are requirement / availability. Shaded cells highlight gaps "
        "(amber ≥ 1 short, red ≥ 2 short). Management pilots count as 0.5.",
        styles["Muted"]))
    story.append(Spacer(1, 6))

    # Build compact table — show up to first 18 months per page, then continue
    MAX_COLS = 12
    for page_start in range(0, horizon, MAX_COLS):
        page_end = min(page_start + MAX_COLS, horizon)
        page_months = labels[page_start:page_end]
        header = ["Fleet · Function"] + page_months
        rows = [header]
        style_ops: list = []
        row_idx = 1
        for f in FLEETS:
            for fn in ("Captain", "First Officer"):
                row = [f"{f} · {fn[:3]}"]
                for m in range(page_start, page_end):
                    r = req[f][fn][m]
                    a = avail[f][fn][m]
                    row.append(f"{r} / {a:.1f}")
                    band = gap_band(max(0.0, r - a))
                    if band == "amber":
                        style_ops.append(("BACKGROUND",
                                          (m - page_start + 1, row_idx),
                                          (m - page_start + 1, row_idx),
                                          colors.HexColor("#FEF3C7")))
                    elif band == "red":
                        style_ops.append(("BACKGROUND",
                                          (m - page_start + 1, row_idx),
                                          (m - page_start + 1, row_idx),
                                          colors.HexColor("#FECACA")))
                rows.append(row)
                row_idx += 1
        col_widths = [40 * mm] + [(246 / len(page_months)) * mm] * len(page_months)
        tbl = _simple_table(rows, col_widths=col_widths, zebra=False)
        extra = TableStyle(style_ops + [
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ])
        tbl.setStyle(extra)
        story.append(tbl)
        if page_end < horizon:
            story.append(Spacer(1, 8))

    story.append(PageBreak())

    # ------------------------- ALL PLANNED ACTIONS CHRONOLOGICAL -------------------------
    story.append(Paragraph("All planned actions (chronological)", styles["H1"]))
    if actions or fleet_changes:
        rows = [["Month", "Category", "Detail", "Duration", "Mode"]]
        combined: list[tuple[int, str, str, str, str]] = []
        for a in actions:
            mo = labels[a.start_month] if 0 <= a.start_month < len(labels) else f"M{a.start_month}"
            if a.action_type == "Type Rating":
                detail = f"{a.from_fleet} {a.from_function} → {a.to_fleet} {a.to_function}"
            elif a.action_type == "Command Upgrade":
                detail = f"{a.from_fleet} FO → {a.to_fleet} CPT"
            elif a.action_type in ("Cadet Hire", "Expat Hire", "Local Hire"):
                detail = f"{a.new_pilot_name or 'TBD'} → {a.to_fleet} {a.to_function}"
            elif a.action_type == "Fleet Change":
                detail = a.note or ""
            else:
                detail = ""
            combined.append((a.start_month, mo, a.action_type, detail,
                             f"{a.duration}mo", a.mode or "—"))
        for c in fleet_changes:
            mo = labels[c.month_index] if 0 <= c.month_index < len(labels) else f"M{c.month_index}"
            verb = "Acquire" if c.delta > 0 else "Dispose"
            combined.append((c.month_index, mo, "Fleet Change",
                             f"{verb} 1× {c.fleet}", "—", "—"))
        combined.sort(key=lambda x: x[0])
        for _, mo, cat, det, dur, md in combined:
            rows.append([mo, cat, det, dur, md])
        story.append(_simple_table(
            rows,
            col_widths=[22 * mm, 34 * mm, 150 * mm, 24 * mm, 26 * mm],
        ))
    else:
        story.append(Paragraph("No actions planned.", styles["Muted"]))
    story.append(PageBreak())

    # ------------------------- CASCADE DIAGRAMS -------------------------
    story.append(Paragraph("Cascade chains — Command Upgrades", styles["H1"]))
    cmd_actions = [a for a in actions if a.action_type == "Command Upgrade"]
    if not cmd_actions:
        story.append(Paragraph("No Command Upgrades planned.", styles["Muted"]))
    else:
        for a in sorted(cmd_actions, key=lambda x: x.start_month):
            mo = labels[a.start_month] if 0 <= a.start_month < len(labels) else f"M{a.start_month}"
            story.append(Paragraph(
                f"{mo} · {a.from_fleet} FO → {a.to_fleet} Captain  ({a.mode})",
                styles["H3"]))
            graph = build_cascade_graph(a, pilots, actions)
            png = _render_cascade_png(graph)
            if png is not None:
                img = Image(BytesIO(png), width=260 * mm, height=90 * mm)
                story.append(KeepTogether([img]))
            else:
                # Fallback: render as a bullet list of nodes
                items = []
                for n in graph["nodes"]:
                    items.append(f"• [{n['kind']}] {n['label'].replace(chr(10), ' — ')}")
                story.append(Paragraph("<br/>".join(items), styles["Body"]))
            story.append(Spacer(1, 6))

    story.append(PageBreak())

    # ------------------------- LOCALISATION ROADMAP -------------------------
    story.append(Paragraph("Localisation roadmap", styles["H1"]))
    story.append(Paragraph(
        f"Current local share: <b>{loc['local_pct']:.1f}%</b> "
        f"({loc['local']} local / {loc['expat']} expat, total {loc['total']}).",
        styles["Body"]))

    # Per-fleet localisation table
    loc_rows = [["Fleet", "Total", "Local", "Expat", "Local %"]]
    for f in FLEETS:
        v = loc["by_fleet"][f]
        pct = (v["local"] / v["total"] * 100) if v["total"] else 0.0
        loc_rows.append([f, str(v["total"]), str(v["local"]),
                         str(v["expat"]), f"{pct:.0f}%"])
    story.append(_simple_table(
        loc_rows,
        col_widths=[40 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm],
    ))

    story.append(Paragraph("Expat positions with eligible local successors", styles["H2"]))
    from cascade_engine import eligible_feeders_for  # local import to avoid cycle
    expats = [p for p in pilots if p.nationality == "Expat"]
    if not expats:
        story.append(Paragraph("No expat positions recorded.", styles["Muted"]))
    else:
        rows = [["Expat position", "Best local candidate", "Route", "Months"]]
        for ex in expats:
            cands = eligible_feeders_for(ex, pilots)
            if cands:
                best = cands[0]
                rows.append([
                    f"{ex.full_name} — {ex.fleet} {ex.function}",
                    best["pilot_name"],
                    best["route"],
                    str(best["duration_months"]),
                ])
            else:
                rows.append([
                    f"{ex.full_name} — {ex.fleet} {ex.function}",
                    "—", "No eligible local feeder yet", "—",
                ])
        story.append(_simple_table(
            rows,
            col_widths=[70 * mm, 55 * mm, 100 * mm, 22 * mm],
        ))

    # Build
    doc.build(story)
    buf.seek(0)
    return buf.getvalue()