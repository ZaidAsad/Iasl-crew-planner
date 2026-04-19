"""
IASL Crew Planning Portal — main Streamlit entry point.

Structure:
    - Session state init (with sample-data seed)
    - Top navigation bar (brand, KPIs, Save/Load/Print)
    - Seven tabs: Dashboard, Registry, Fleet Planner, Timeline,
      Action Planner, Localisation, Print Plan
"""

from __future__ import annotations

import json
from datetime import date
from io import StringIO

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from cascade_engine import (
    FLEETS, FUNCTIONS, NATIONALITIES, DESIGNATIONS, PILOT_STATUSES,
    ACTION_TYPES, CREW_SETS_PER_AIRCRAFT, DEFAULT_AIRCRAFT_COUNTS,
    TRAINING_DURATIONS,
    Pilot, PlannedAction, FleetChange,
    month_labels, month_index_to_label,
    resolve_aircraft_counts, fleet_requirement,
    compute_availability, compute_gaps, gap_band,
    build_cascade_graph, detect_conflicts,
    eligible_feeders_for, localisation_summary,
    serialise_state, deserialise_state, new_id,
)
from pdf_export import build_pdf
from styling import (
    COLORS, FLEET_COLORS, status_color, status_label,
    inject_css, register_plotly_theme,
    metric_card, fleet_card, pill, section_header, info_panel,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="IASL Crew Planning Portal",
    page_icon="✈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject_css()
register_plotly_theme()


# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------
def _seed_sample_pilots() -> list[Pilot]:
    """Seed a representative crew. Values are illustrative, not operational."""
    p: list[Pilot] = []

    def add(fleet, fn, local_count, expat_count, mgmt_count=0, designations=None):
        des = designations or []
        ctr = len(p) + 1
        for i in range(local_count):
            p.append(Pilot(
                employee_id=f"L{ctr + i:03d}",
                full_name=f"{fleet} {fn[:3]} Local {i+1}",
                nationality="Local",
                fleet=fleet, function=fn,
                designations=des if i == 0 else [],
                management=(i < mgmt_count),
                status="Active",
            ))
        base = len(p) + 1
        for i in range(expat_count):
            p.append(Pilot(
                employee_id=f"E{base + i:03d}",
                full_name=f"{fleet} {fn[:3]} Expat {i+1}",
                nationality="Expat",
                fleet=fleet, function=fn,
                designations=[],
                management=False,
                status="Active",
            ))

    # A330: 1 AC × 7 sets = 7 CPT + 7 FO. Mostly expat.
    add("A330", "Captain", local_count=2, expat_count=5, mgmt_count=1, designations=["TRI"])
    add("A330", "First Officer", local_count=3, expat_count=4)

    # A320: 1 AC × 5 sets = 5 CPT + 5 FO.
    add("A320", "Captain", local_count=3, expat_count=2, mgmt_count=1, designations=["TRE"])
    add("A320", "First Officer", local_count=4, expat_count=1)

    # ATR72: 5 AC × 6 sets = 30 CPT + 30 FO. Mostly local.
    add("ATR72", "Captain", local_count=22, expat_count=8, mgmt_count=2, designations=["TRE", "LI"])
    add("ATR72", "First Officer", local_count=26, expat_count=4)

    # DHC8: 3 AC × 5 sets = 15 CPT + 15 FO. Phasing out.
    add("DHC8", "Captain", local_count=10, expat_count=5)
    add("DHC8", "First Officer", local_count=12, expat_count=3)

    return p


def _init_state():
    ss = st.session_state
    if "initialised" in ss:
        return
    today = date.today()
    ss.start_year = today.year
    ss.start_month = today.month
    ss.horizon = 24
    ss.initial_aircraft = dict(DEFAULT_AIRCRAFT_COUNTS)
    ss.pilots = _seed_sample_pilots()
    ss.fleet_changes = []  # list[FleetChange]
    ss.actions = []        # list[PlannedAction]
    ss.current_tab = "Dashboard"
    ss.initialised = True


_init_state()


# ---------------------------------------------------------------------------
# Derived state helpers (recomputed on every render — cheap for this scale)
# ---------------------------------------------------------------------------
def derived():
    ss = st.session_state
    labels = month_labels(ss.start_year, ss.start_month, ss.horizon)
    ac_counts = resolve_aircraft_counts(ss.initial_aircraft, ss.fleet_changes, ss.horizon)
    req = fleet_requirement(ac_counts)
    avail = compute_availability(ss.pilots, ss.actions, ss.horizon)
    gaps = compute_gaps(req, avail)
    conflicts = detect_conflicts(ss.actions)
    loc = localisation_summary(ss.pilots)
    return {
        "labels": labels,
        "ac_counts": ac_counts,
        "req": req,
        "avail": avail,
        "gaps": gaps,
        "conflicts": conflicts,
        "loc": loc,
    }


def current_state_payload() -> dict:
    ss = st.session_state
    return {
        "start_year": ss.start_year,
        "start_month": ss.start_month,
        "horizon": ss.horizon,
        "initial_aircraft": ss.initial_aircraft,
        "pilots": ss.pilots,
        "fleet_changes": ss.fleet_changes,
        "actions": ss.actions,
    }


# ---------------------------------------------------------------------------
# Top navigation bar
# ---------------------------------------------------------------------------
def render_topbar():
    ss = st.session_state
    d = derived()
    total_pilots = len(ss.pilots)
    total_aircraft = sum(ss.initial_aircraft.values())
    period = f"{d['labels'][0]} → {d['labels'][-1]}" if d["labels"] else "—"

    # Outer HTML bar
    st.markdown(
        f"""
        <div class="iasl-topbar">
          <div class="iasl-brand">
            <div class="iasl-logo">IASL</div>
            <div>
              <div class="iasl-title">Crew Planning Portal</div>
              <div class="iasl-subtitle">Island Aviation Services Limited</div>
            </div>
          </div>
          <div class="iasl-nav-stats">
            <div class="iasl-nav-stat">
              <div class="iasl-nav-stat-label">Pilots</div>
              <div class="iasl-nav-stat-value">{total_pilots}</div>
            </div>
            <div class="iasl-nav-stat">
              <div class="iasl-nav-stat-label">Aircraft</div>
              <div class="iasl-nav-stat-value">{total_aircraft}</div>
            </div>
            <div class="iasl-nav-stat">
              <div class="iasl-nav-stat-label">Planning period</div>
              <div class="iasl-nav-stat-value" style="font-size:14px;">{period}</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Action buttons (Streamlit widgets row — cannot be inside raw HTML)
    c1, c2, c3, c4 = st.columns([1, 1, 1, 6])

    with c1:
        payload = json.dumps(
            serialise_state(current_state_payload()),
            indent=2, default=str,
        )
        st.download_button(
            "💾 Save JSON",
            data=payload,
            file_name=f"iasl_crew_plan_{date.today().isoformat()}.json",
            mime="application/json",
            use_container_width=True,
        )

    with c2:
        uploaded = st.file_uploader(
            "Load JSON", type=["json"],
            label_visibility="collapsed",
            key="json_uploader",
        )
        if uploaded is not None:
            try:
                data = json.loads(uploaded.read().decode("utf-8"))
                restored = deserialise_state(data)
                for k, v in restored.items():
                    st.session_state[k] = v
                st.success("Plan restored from JSON.")
            except Exception as e:
                st.error(f"Failed to load JSON: {e}")

    with c3:
        if st.button("🖨 Print PDF", use_container_width=True, type="primary"):
            with st.spinner("Generating PDF…"):
                try:
                    pdf_bytes = build_pdf(current_state_payload())
                    st.session_state["pdf_bytes"] = pdf_bytes
                    st.success("PDF ready — download below.")
                except Exception as e:
                    st.error(f"PDF generation failed: {e}")

    if st.session_state.get("pdf_bytes"):
        st.download_button(
            "⬇ Download PDF",
            data=st.session_state["pdf_bytes"],
            file_name=f"iasl_crew_plan_{date.today().isoformat()}.pdf",
            mime="application/pdf",
        )


# ---------------------------------------------------------------------------
# TAB 1 — Dashboard
# ---------------------------------------------------------------------------
def tab_dashboard():
    ss = st.session_state
    d = derived()

    section_header("Overview")

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        metric_card("Total pilots", len(ss.pilots))
    with c2:
        metric_card("Total aircraft", sum(ss.initial_aircraft.values()))
    with c3:
        metric_card("Localisation", f"{d['loc']['local_pct']:.0f}%",
                    f"{d['loc']['local']} local · {d['loc']['expat']} expat")
    with c4:
        metric_card("Planned actions", len(ss.actions))
    with c5:
        critical_months = sum(
            1 for f in FLEETS for fn in FUNCTIONS
            for m in range(ss.horizon)
            if gap_band(d["gaps"][f][fn][m]) == "red"
        )
        metric_card("Red-band cells", critical_months,
                    "across all fleets & months")

    # Fleet status cards (month 1 snapshot)
    section_header("Fleet status (month 1)")
    cols = st.columns(4)
    for i, f in enumerate(FLEETS):
        with cols[i]:
            cap_req = d["req"][f]["Captain"][0]
            fo_req  = d["req"][f]["First Officer"][0]
            cap_av  = d["avail"][f]["Captain"][0]
            fo_av   = d["avail"][f]["First Officer"][0]
            req_total = cap_req + fo_req
            av_total  = cap_av + fo_av
            worst = max(d["gaps"][f]["Captain"][0], d["gaps"][f]["First Officer"][0])
            band = gap_band(worst)
            fleet_card(f, req_total, av_total, d["ac_counts"][f][0], band)

    # Warnings
    section_header("Warnings & conflicts")
    any_warn = False
    if d["conflicts"]:
        for c in d["conflicts"]:
            info_panel(f"⚠ <b>Conflict:</b> {c['reason']}", kind="warn")
            any_warn = True

    # Red gap warnings
    red_warnings = []
    for f in FLEETS:
        for fn in FUNCTIONS:
            for m in range(ss.horizon):
                if gap_band(d["gaps"][f][fn][m]) == "red":
                    red_warnings.append(
                        f"{d['labels'][m]} — {f} {fn}: short {d['gaps'][f][fn][m]:.1f}"
                    )
                    break  # one per fleet/function is enough noise
    if red_warnings:
        info_panel("🔴 <b>Red-band shortfalls detected:</b><br>" +
                   "<br>".join(red_warnings[:8]) +
                   ("<br>…" if len(red_warnings) > 8 else ""),
                   kind="error")
        any_warn = True

    # TBD trainees
    tbd_count = sum(
        1 for a in ss.actions
        for t in a.trainee_ids
        if t.startswith("TBD")
    )
    if tbd_count:
        info_panel(f"ℹ {tbd_count} TBD trainee slot(s) across all planned actions. "
                   "Assign names when known.", kind="info")
        any_warn = True

    if not any_warn:
        info_panel("✓ No warnings. Plan looks clean.", kind="info")

    # Gap heatmap
    section_header("Gap heatmap across the planning horizon")
    _render_gap_heatmap(d)


def _render_gap_heatmap(d):
    """Compact heatmap of gap bands: rows = fleet·function, cols = months."""
    rows: list[str] = []
    z: list[list[float]] = []
    texts: list[list[str]] = []
    for f in FLEETS:
        for fn in FUNCTIONS:
            rows.append(f"{f} · {fn[:3]}")
            row_vals = []
            row_texts = []
            for m in range(len(d["labels"])):
                g = d["gaps"][f][fn][m]
                # Encode: 0 = green, 1 = amber, 2 = red
                if g < 1:
                    row_vals.append(0)
                elif g < 2:
                    row_vals.append(1)
                else:
                    row_vals.append(2)
                row_texts.append(
                    f"{f} {fn}<br>{d['labels'][m]}<br>"
                    f"Req {d['req'][f][fn][m]}  ·  Avl {d['avail'][f][fn][m]:.1f}<br>"
                    f"Gap {g:.1f}"
                )
            z.append(row_vals)
            texts.append(row_texts)

    fig = go.Figure(data=go.Heatmap(
        z=z, x=d["labels"], y=rows,
        colorscale=[
            [0.0, COLORS["green"]],
            [0.5, COLORS["amber"]],
            [1.0, COLORS["red"]],
        ],
        zmin=0, zmax=2,
        showscale=False,
        text=texts,
        hoverinfo="text",
        xgap=2, ygap=3,
    ))
    fig.update_layout(height=320, margin=dict(l=80, r=20, t=10, b=60))
    fig.update_xaxes(tickangle=-45, tickfont=dict(size=10))
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# TAB 2 — Pilot Registry
# ---------------------------------------------------------------------------
def tab_registry():
    ss = st.session_state
    section_header("Pilot registry")

    # Summary bar
    total = len(ss.pilots)
    active = sum(1 for p in ss.pilots if p.status == "Active")
    mgmt   = sum(1 for p in ss.pilots if p.management)
    local  = sum(1 for p in ss.pilots if p.nationality == "Local")
    expat  = sum(1 for p in ss.pilots if p.nationality == "Expat")

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: metric_card("Total", total)
    with c2: metric_card("Active", active)
    with c3: metric_card("Management", mgmt, "count 0.5 each")
    with c4: metric_card("Local", local)
    with c5: metric_card("Expat", expat)

    # Filters
    section_header("Filter")
    f1, f2, f3, f4, f5 = st.columns(5)
    with f1:
        f_fleet = st.multiselect("Fleet", FLEETS, default=FLEETS, key="reg_f_fleet")
    with f2:
        f_func = st.multiselect("Function", FUNCTIONS, default=FUNCTIONS, key="reg_f_func")
    with f3:
        f_nat = st.multiselect("Nationality", NATIONALITIES, default=NATIONALITIES, key="reg_f_nat")
    with f4:
        f_status = st.multiselect("Status", PILOT_STATUSES, default=PILOT_STATUSES, key="reg_f_status")
    with f5:
        f_mgmt = st.selectbox("Management", ["All", "Yes", "No"], key="reg_f_mgmt")

    def _match(p: Pilot) -> bool:
        if p.fleet not in f_fleet: return False
        if p.function not in f_func: return False
        if p.nationality not in f_nat: return False
        if p.status not in f_status: return False
        if f_mgmt == "Yes" and not p.management: return False
        if f_mgmt == "No" and p.management: return False
        return True

    filtered = [p for p in ss.pilots if _match(p)]

    # Table
    section_header(f"Pilots ({len(filtered)})")
    if filtered:
        df = pd.DataFrame([{
            "ID": p.employee_id,
            "Name": p.full_name,
            "Fleet": p.fleet,
            "Function": p.function,
            "Nationality": p.nationality,
            "Designations": ", ".join(p.designations) if p.designations else "—",
            "Mgmt": "Yes" if p.management else "No",
            "Weight": f"{p.contribution():.1f}",
            "Status": p.status,
        } for p in filtered])
        st.dataframe(df, use_container_width=True, hide_index=True, height=360)
    else:
        info_panel("No pilots match the current filters.")

    # Add / edit / delete / CSV
    section_header("Manage pilots")
    tab_add, tab_edit, tab_delete, tab_csv = st.tabs(
        ["➕ Add pilot", "✎ Edit pilot", "🗑 Delete pilot", "📥 CSV import/export"]
    )

    with tab_add:
        with st.form("add_pilot", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                emp_id = st.text_input("Employee ID", value=new_id("P"))
                full_name = st.text_input("Full name")
                nationality = st.selectbox("Nationality", NATIONALITIES)
            with c2:
                fleet = st.selectbox("Fleet", FLEETS)
                function = st.selectbox("Function", FUNCTIONS)
                status = st.selectbox("Status", PILOT_STATUSES)
            with c3:
                designations = st.multiselect("Designations", DESIGNATIONS)
                management = st.checkbox("Management Pilot (counts 0.5)")
            submitted = st.form_submit_button("Add pilot", type="primary")
            if submitted:
                if not full_name.strip():
                    st.error("Name is required.")
                elif any(p.employee_id == emp_id for p in ss.pilots):
                    st.error("Employee ID already exists.")
                else:
                    ss.pilots.append(Pilot(
                        employee_id=emp_id, full_name=full_name.strip(),
                        nationality=nationality, fleet=fleet, function=function,
                        designations=designations, management=management, status=status,
                    ))
                    st.success(f"Added {full_name}.")
                    st.rerun()

    with tab_edit:
        if not ss.pilots:
            st.info("No pilots to edit.")
        else:
            options = {f"{p.employee_id} — {p.full_name}": p for p in ss.pilots}
            key = st.selectbox("Select pilot to edit", list(options.keys()))
            p = options[key]
            with st.form("edit_pilot"):
                c1, c2, c3 = st.columns(3)
                with c1:
                    new_name = st.text_input("Full name", value=p.full_name)
                    new_nat = st.selectbox("Nationality", NATIONALITIES,
                                           index=NATIONALITIES.index(p.nationality))
                with c2:
                    new_fleet = st.selectbox("Fleet", FLEETS, index=FLEETS.index(p.fleet))
                    new_func = st.selectbox("Function", FUNCTIONS,
                                            index=FUNCTIONS.index(p.function))
                    new_status = st.selectbox("Status", PILOT_STATUSES,
                                              index=PILOT_STATUSES.index(p.status))
                with c3:
                    new_desig = st.multiselect("Designations", DESIGNATIONS,
                                               default=p.designations)
                    new_mgmt = st.checkbox("Management Pilot", value=p.management)
                if st.form_submit_button("Save changes", type="primary"):
                    p.full_name = new_name.strip()
                    p.nationality = new_nat
                    p.fleet = new_fleet
                    p.function = new_func
                    p.status = new_status
                    p.designations = new_desig
                    p.management = new_mgmt
                    st.success("Saved.")
                    st.rerun()

    with tab_delete:
        if not ss.pilots:
            st.info("No pilots to delete.")
        else:
            options = {f"{p.employee_id} — {p.full_name}": p for p in ss.pilots}
            key = st.selectbox("Select pilot to delete", list(options.keys()),
                               key="del_pilot_sel")
            if st.button("Delete pilot", type="primary"):
                ss.pilots = [x for x in ss.pilots if x.employee_id != options[key].employee_id]
                st.success(f"Deleted {options[key].full_name}.")
                st.rerun()

    with tab_csv:
        st.markdown("**Export** all pilots as CSV:")
        if ss.pilots:
            df = pd.DataFrame([{
                "employee_id": p.employee_id,
                "full_name": p.full_name,
                "nationality": p.nationality,
                "fleet": p.fleet,
                "function": p.function,
                "designations": "|".join(p.designations),
                "management": p.management,
                "status": p.status,
            } for p in ss.pilots])
            st.download_button(
                "Download CSV",
                data=df.to_csv(index=False),
                file_name="iasl_pilots.csv",
                mime="text/csv",
            )

        st.markdown("---")
        st.markdown("**Import** pilots from CSV")
        st.caption(
            "Required columns: employee_id, full_name, nationality, fleet, function. "
            "Optional: designations (pipe-separated, e.g. TRE|LI), management (true/false), status. "
            "Values must match: nationality ∈ {Local, Expat}; fleet ∈ {A330, A320, ATR72, DHC8}; "
            "function ∈ {Captain, First Officer}; status ∈ {Active, On Type Rating, On Leave}."
        )

        # Template download
        template_df = pd.DataFrame([
            {"employee_id": "P001", "full_name": "Example Captain",
             "nationality": "Local", "fleet": "ATR72", "function": "Captain",
             "designations": "TRE|LI", "management": False, "status": "Active"},
            {"employee_id": "P002", "full_name": "Example First Officer",
             "nationality": "Expat", "fleet": "A320", "function": "First Officer",
             "designations": "", "management": False, "status": "Active"},
        ])
        st.download_button(
            "📄 Download CSV template",
            data=template_df.to_csv(index=False),
            file_name="iasl_pilots_template.csv",
            mime="text/csv",
        )

        # Import options
        replace_mode = st.checkbox(
            "Replace existing registry (delete all current pilots first)",
            value=False, key="csv_replace_mode",
        )

        up = st.file_uploader(
            "Upload CSV",
            type=["csv"],
            key=f"csv_up_{st.session_state.get('csv_upload_counter', 0)}",
        )

        if up is not None:
            try:
                # Parse
                df = pd.read_csv(up, dtype=str, keep_default_na=False)
                df.columns = [c.strip().lower() for c in df.columns]

                # Validate required columns
                required = {"employee_id", "full_name", "nationality",
                            "fleet", "function"}
                missing = required - set(df.columns)
                if missing:
                    st.error(f"CSV is missing required columns: {', '.join(sorted(missing))}")
                else:
                    # Validation pass
                    valid_fleets = set(FLEETS)
                    valid_functions = set(FUNCTIONS)
                    valid_nationalities = set(NATIONALITIES)
                    valid_statuses = set(PILOT_STATUSES)

                    errors = []
                    new_pilots = []

                    for idx, r in df.iterrows():
                        row_num = idx + 2  # account for header row
                        eid = str(r["employee_id"]).strip()
                        name = str(r["full_name"]).strip()
                        nat = str(r["nationality"]).strip()
                        fleet = str(r["fleet"]).strip()
                        func = str(r["function"]).strip()

                        if not eid:
                            errors.append(f"Row {row_num}: empty employee_id")
                            continue
                        if not name:
                            errors.append(f"Row {row_num}: empty full_name")
                            continue
                        if nat not in valid_nationalities:
                            errors.append(
                                f"Row {row_num}: nationality '{nat}' invalid "
                                f"(must be Local or Expat)"
                            )
                            continue
                        if fleet not in valid_fleets:
                            errors.append(
                                f"Row {row_num}: fleet '{fleet}' invalid "
                                f"(must be one of {', '.join(sorted(valid_fleets))})"
                            )
                            continue
                        if func not in valid_functions:
                            errors.append(
                                f"Row {row_num}: function '{func}' invalid "
                                f"(must be Captain or First Officer)"
                            )
                            continue

                        # Optional columns
                        desig_raw = str(r.get("designations", "")).strip()
                        desigs = [d.strip() for d in desig_raw.split("|") if d.strip()]

                        mgmt_raw = str(r.get("management", "")).strip().lower()
                        mgmt = mgmt_raw in ("true", "1", "yes", "y", "t")

                        status = str(r.get("status", "Active")).strip() or "Active"
                        if status not in valid_statuses:
                            errors.append(
                                f"Row {row_num}: status '{status}' invalid "
                                f"(must be one of {', '.join(sorted(valid_statuses))})"
                            )
                            continue

                        new_pilots.append(Pilot(
                            employee_id=eid,
                            full_name=name,
                            nationality=nat,
                            fleet=fleet,
                            function=func,
                            designations=desigs,
                            management=mgmt,
                            status=status,
                        ))

                    # Preview
                    st.markdown(f"**Parsed {len(new_pilots)} valid row(s) "
                                f"from {len(df)} total.**")

                    if errors:
                        with st.expander(f"⚠ {len(errors)} row(s) had errors (click to view)"):
                            for e in errors[:50]:
                                st.markdown(f"- {e}")
                            if len(errors) > 50:
                                st.markdown(f"…and {len(errors) - 50} more.")

                    if new_pilots:
                        preview_df = pd.DataFrame([{
                            "ID": p.employee_id,
                            "Name": p.full_name,
                            "Fleet": p.fleet,
                            "Function": p.function,
                            "Nationality": p.nationality,
                            "Mgmt": "Yes" if p.management else "No",
                            "Status": p.status,
                        } for p in new_pilots[:20]])
                        st.markdown("**Preview (first 20 rows):**")
                        st.dataframe(preview_df, use_container_width=True,
                                     hide_index=True)

                        # Commit button
                        if st.button(
                            f"✓ Confirm import of {len(new_pilots)} pilot(s)",
                            type="primary",
                            key="csv_commit",
                        ):
                            if replace_mode:
                                ss.pilots = []
                            # Deduplicate by employee_id
                            existing_ids = {p.employee_id for p in ss.pilots}
                            added = 0
                            skipped = 0
                            for p in new_pilots:
                                if p.employee_id in existing_ids:
                                    skipped += 1
                                    continue
                                ss.pilots.append(p)
                                existing_ids.add(p.employee_id)
                                added += 1

                            msg = f"Imported {added} pilot(s)."
                            if skipped > 0:
                                msg += f" Skipped {skipped} duplicate ID(s)."
                            st.success(msg)

                            # Bump the uploader key so the widget resets
                            st.session_state["csv_upload_counter"] = (
                                st.session_state.get("csv_upload_counter", 0) + 1
                            )
                            st.rerun()

            except pd.errors.EmptyDataError:
                st.error("The uploaded file is empty.")
            except pd.errors.ParserError as e:
                st.error(f"Could not parse CSV: {e}")
            except Exception as e:
                st.error(f"Import failed: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# TAB 3 — Fleet Planner
# ---------------------------------------------------------------------------
def tab_fleet_planner():
    ss = st.session_state
    d = derived()

    section_header("Initial fleet (month 1)")
    c1, c2, c3, c4 = st.columns(4)
    for i, f in enumerate(FLEETS):
        with [c1, c2, c3, c4][i]:
            v = st.number_input(
                f, min_value=0, max_value=30,
                value=ss.initial_aircraft[f],
                key=f"init_ac_{f}",
            )
            ss.initial_aircraft[f] = v

    # Horizon / start
    section_header("Planning window")
    h1, h2, h3 = st.columns(3)
    with h1:
        ss.start_year = st.number_input(
            "Start year", min_value=2024, max_value=2040,
            value=ss.start_year, key="sy",
        )
    with h2:
        ss.start_month = st.number_input(
            "Start month", min_value=1, max_value=12,
            value=ss.start_month, key="sm",
        )
    with h3:
        ss.horizon = st.slider(
            "Horizon (months)", min_value=6, max_value=60,
            value=ss.horizon, key="hz",
        )

    # Add a buy/sell action
    section_header("Add fleet change")
    with st.form("add_fleet_change", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([1, 1, 2, 2])
        with c1:
            fc_fleet = st.selectbox("Fleet", FLEETS, key="fc_fleet")
        with c2:
            fc_action = st.selectbox("Action", ["Acquire", "Dispose"], key="fc_action")
        with c3:
            fc_month = st.selectbox(
                "Month",
                options=list(range(ss.horizon)),
                format_func=lambda i: f"{i+1:2d}. {month_index_to_label(ss.start_year, ss.start_month, i)}",
                key="fc_month",
            )
        with c4:
            fc_note = st.text_input("Note (optional)", key="fc_note")
        if st.form_submit_button("Add fleet change", type="primary"):
            delta = 1 if fc_action == "Acquire" else -1
            ss.fleet_changes.append(FleetChange(
                id=new_id("fc"), fleet=fc_fleet,
                month_index=fc_month, delta=delta, note=fc_note,
            ))
            st.success(f"{fc_action} 1× {fc_fleet} at month {fc_month+1}.")
            st.rerun()

    # Existing changes
    if ss.fleet_changes:
        section_header("Scheduled fleet changes")
        for c in sorted(ss.fleet_changes, key=lambda x: x.month_index):
            cc1, cc2 = st.columns([10, 1])
            with cc1:
                verb = "Acquire" if c.delta > 0 else "Dispose"
                color = "green" if c.delta > 0 else "amber"
                st.markdown(
                    f"{pill(verb, color)} &nbsp; "
                    f"<b>{c.fleet}</b> &nbsp; at &nbsp; "
                    f"<b>{month_index_to_label(ss.start_year, ss.start_month, c.month_index)}</b>"
                    f" &nbsp; <span style='color:{COLORS['text_muted']}'>{c.note}</span>",
                    unsafe_allow_html=True,
                )
            with cc2:
                if st.button("✕", key=f"del_fc_{c.id}"):
                    ss.fleet_changes = [x for x in ss.fleet_changes if x.id != c.id]
                    st.rerun()

    # Monthly aircraft grid
    section_header("Monthly aircraft count")
    rows = []
    for f in FLEETS:
        rows.append([f] + d["ac_counts"][f])
    df = pd.DataFrame(rows, columns=["Fleet"] + d["labels"])
    st.dataframe(df, use_container_width=True, hide_index=True, height=200)

    # Chart: aircraft count over time
    fig = go.Figure()
    for f in FLEETS:
        fig.add_trace(go.Scatter(
            x=d["labels"], y=d["ac_counts"][f],
            name=f, mode="lines+markers",
            line=dict(color=FLEET_COLORS[f], width=2.5),
            marker=dict(size=6),
        ))
    fig.update_layout(
        height=320,
        xaxis_title="Month", yaxis_title="Aircraft",
        hovermode="x unified",
    )
    fig.update_xaxes(tickangle=-45)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# TAB 4 — Planning Timeline
# ---------------------------------------------------------------------------
def tab_timeline():
    ss = st.session_state
    d = derived()

    section_header("Requirement vs availability")
    sel_fleet = st.selectbox("Fleet", FLEETS, key="tl_fleet")
    sel_func = st.selectbox("Function", ["Both"] + FUNCTIONS, key="tl_func")

    funcs = FUNCTIONS if sel_func == "Both" else [sel_func]

    fig = go.Figure()
    for fn in funcs:
        req = d["req"][sel_fleet][fn]
        av  = d["avail"][sel_fleet][fn]
        fig.add_trace(go.Scatter(
            x=d["labels"], y=req, name=f"{fn} required",
            mode="lines", line=dict(dash="dash", width=2,
                                    color=FLEET_COLORS[sel_fleet]),
        ))
        fig.add_trace(go.Scatter(
            x=d["labels"], y=av, name=f"{fn} available",
            mode="lines+markers",
            line=dict(width=2.5, color=FLEET_COLORS[sel_fleet]),
            fill="tonexty" if len(fig.data) > 0 else None,
            fillcolor="rgba(239,68,68,0.08)",
        ))
    fig.update_layout(
        height=360, hovermode="x unified",
        xaxis_title="Month", yaxis_title="Pilots",
    )
    fig.update_xaxes(tickangle=-45)
    st.plotly_chart(fig, use_container_width=True)

    # Overlay of planned actions for this fleet
    section_header("Planned actions touching this fleet")
    rel = [a for a in ss.actions if sel_fleet in (a.from_fleet, a.to_fleet)]
    if not rel:
        info_panel("No planned actions touch this fleet.")
    else:
        rows = []
        for a in sorted(rel, key=lambda x: x.start_month):
            detail = ""
            if a.action_type == "Type Rating":
                detail = f"{a.from_fleet} {a.from_function} → {a.to_fleet} {a.to_function}"
            elif a.action_type == "Command Upgrade":
                detail = f"{a.from_fleet} FO → {a.to_fleet} CPT"
            elif a.action_type in ("Cadet Hire", "Expat Hire", "Local Hire"):
                detail = f"{a.new_pilot_name or 'TBD'} → {a.to_fleet} {a.to_function}"
            rows.append({
                "Month": d["labels"][a.start_month] if 0 <= a.start_month < len(d["labels"]) else f"M{a.start_month}",
                "Type": a.action_type,
                "Detail": detail,
                "Duration": f"{a.duration}mo",
                "Mode": a.mode,
                "Trainees": ", ".join(a.trainee_ids) if a.trainee_ids else "—",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Full grid
    section_header("Full grid (all fleets, all functions)")
    grid_rows = []
    for f in FLEETS:
        for fn in FUNCTIONS:
            row = {"Fleet": f, "Function": fn}
            for i, lbl in enumerate(d["labels"]):
                req = d["req"][f][fn][i]
                av  = d["avail"][f][fn][i]
                row[lbl] = f"{req}/{av:.1f}"
            grid_rows.append(row)
    gdf = pd.DataFrame(grid_rows)
    st.dataframe(gdf, use_container_width=True, hide_index=True, height=360)


# ---------------------------------------------------------------------------
# TAB 5 — Action Planner
# ---------------------------------------------------------------------------
def tab_action_planner():
    ss = st.session_state
    d = derived()

    section_header("Add action")

    action_type = st.selectbox("Action type", ACTION_TYPES, key="new_action_type")

    with st.form("add_action", clear_on_submit=False):
        if action_type == "Type Rating":
            _form_type_rating(d)
        elif action_type == "Command Upgrade":
            _form_command_upgrade(d)
        elif action_type == "Cadet Hire":
            _form_hire(d, "Cadet Hire")
        elif action_type == "Expat Hire":
            _form_hire(d, "Expat Hire")
        elif action_type == "Local Hire":
            _form_hire(d, "Local Hire")
        elif action_type == "Fleet Change":
            _form_fleet_change(d)

    # Conflicts
    if d["conflicts"]:
        section_header("Conflicts")
        for c in d["conflicts"]:
            info_panel(f"⚠ {c['reason']}", kind="warn")

    # Existing actions & cascades
    section_header("Scheduled actions")
    if not ss.actions:
        info_panel("No actions yet. Add one above.")
        return

    for a in sorted(ss.actions, key=lambda x: x.start_month):
        mo = d["labels"][a.start_month] if 0 <= a.start_month < len(d["labels"]) else f"M{a.start_month}"
        title = f"{mo}  ·  {a.action_type}"
        if a.action_type == "Type Rating":
            title += f"  ·  {a.from_fleet} {a.from_function} → {a.to_fleet} {a.to_function}"
        elif a.action_type == "Command Upgrade":
            title += f"  ·  {a.from_fleet} FO → {a.to_fleet} CPT"
        elif a.action_type in ("Cadet Hire", "Expat Hire", "Local Hire"):
            title += f"  ·  → {a.to_fleet} {a.to_function} ({a.new_pilot_name or 'TBD'})"
        elif a.action_type == "Fleet Change":
            title += f"  ·  {a.from_fleet}"

        with st.expander(title):
            c1, c2 = st.columns([5, 1])
            with c1:
                st.markdown(
                    f"**Duration:** {a.duration}mo  &nbsp;  "
                    f"**Mode:** {a.mode}  &nbsp;  "
                    f"**Instructor:** {a.instructor_id or '—'}  &nbsp;  "
                    f"**Trainees:** {', '.join(a.trainee_ids) if a.trainee_ids else '—'}"
                )
                if a.note:
                    st.markdown(f"_{a.note}_")
                # Cascade
                graph = build_cascade_graph(a, ss.pilots, ss.actions)
                _render_cascade_plot(graph, key_suffix=a.id)
            with c2:
                if st.button("🗑 Delete", key=f"del_action_{a.id}"):
                    ss.actions = [x for x in ss.actions if x.id != a.id]
                    st.rerun()


def _month_selector(d, key: str) -> int:
    return st.selectbox(
        "Start month",
        options=list(range(st.session_state.horizon)),
        format_func=lambda i: f"{i+1:2d}. {d['labels'][i]}" if i < len(d['labels']) else str(i),
        key=key,
    )


def _pilot_picker(
    label: str, key: str,
    fleet_filter: list[str] | None = None,
    function_filter: list[str] | None = None,
    nationality_filter: list[str] | None = None,
    allow_tbd: bool = True,
    max_selections: int | None = None,
) -> list[str]:
    """Return a list of pilot employee_ids plus optional TBD placeholder(s)."""
    ss = st.session_state
    pool = ss.pilots
    if fleet_filter:
        pool = [p for p in pool if p.fleet in fleet_filter]
    if function_filter:
        pool = [p for p in pool if p.function in function_filter]
    if nationality_filter:
        pool = [p for p in pool if p.nationality in nationality_filter]

    options_map = {f"{p.employee_id} — {p.full_name} ({p.fleet} {p.function})": p.employee_id
                   for p in pool}
    if allow_tbd:
        options_map["TBD-1 (placeholder)"] = "TBD-1"
        options_map["TBD-2 (placeholder)"] = "TBD-2"

    selected = st.multiselect(
        label, options=list(options_map.keys()), key=key,
        max_selections=max_selections,
    )
    return [options_map[s] for s in selected]


def _form_type_rating(d):
    ss = st.session_state
    c1, c2, c3 = st.columns(3)
    with c1:
        from_fleet = st.selectbox("From fleet", FLEETS, key="tr_from_fleet")
        from_function = st.selectbox("From function", FUNCTIONS, key="tr_from_func")
    with c2:
        to_fleet = st.selectbox("To fleet", FLEETS, key="tr_to_fleet")
        to_function = st.selectbox("To function", FUNCTIONS, key="tr_to_func")
    with c3:
        mode = st.selectbox("Mode", ["External", "Internal"], key="tr_mode")
        start = _month_selector(d, "tr_start")

    # Suggest duration
    duration = _suggest_duration("Type Rating",
                                 from_fleet, from_function,
                                 to_fleet, to_function)
    duration = st.number_input("Duration (months)", min_value=1, max_value=12,
                               value=duration, key="tr_dur")

    st.markdown("**Trainees** (up to 2, pick pilots or TBD placeholders)")
    trainees = _pilot_picker(
        "Select trainees", "tr_trainees",
        fleet_filter=[from_fleet], function_filter=[from_function],
        max_selections=2,
    )

    instructor = ""
    if mode == "Internal":
        st.markdown("**Instructor** (from destination fleet, must be a Captain)")
        inst_list = _pilot_picker(
            "Instructor", "tr_instructor",
            fleet_filter=[to_fleet], function_filter=["Captain"],
            max_selections=1,
        )
        instructor = inst_list[0] if inst_list else ""

    note = st.text_input("Note (optional)", key="tr_note")

    if st.form_submit_button("Add Type Rating", type="primary"):
        if not trainees:
            st.error("Pick at least one trainee.")
        else:
            ss.actions.append(PlannedAction(
                id=new_id("act"), action_type="Type Rating",
                start_month=start, duration=duration, mode=mode,
                instructor_id=instructor, trainee_ids=trainees,
                from_fleet=from_fleet, from_function=from_function,
                to_fleet=to_fleet, to_function=to_function,
                note=note,
            ))
            st.success("Type Rating added.")
            st.rerun()


def _form_command_upgrade(d):
    ss = st.session_state
    c1, c2, c3 = st.columns(3)
    with c1:
        to_fleet = st.selectbox("To fleet (Captain)", FLEETS, key="cu_to_fleet")
    with c2:
        mode = st.selectbox("Mode", ["External", "Internal"], key="cu_mode")
    with c3:
        start = _month_selector(d, "cu_start")

    # Eligibility is fleet-dependent — per spec
    eligible_fleets: list[str] = []
    eligible_functions: list[str] = []
    if to_fleet == "A330":
        # A330 FOs or A320 Captains
        eligible_fleets = ["A330", "A320"]
        eligible_functions = ["First Officer", "Captain"]
    elif to_fleet == "A320":
        # A320 FOs or A330 FOs (the A330 FO path is a compound action)
        eligible_fleets = ["A320", "A330"]
        eligible_functions = ["First Officer"]
    else:
        # Same-fleet only for ATR72 / DHC8
        eligible_fleets = [to_fleet]
        eligible_functions = ["First Officer"]

    st.caption(
        f"Eligible pool for {to_fleet} Captain upgrade: "
        f"{', '.join(eligible_fleets)} · {', '.join(eligible_functions)}"
    )

    trainees = _pilot_picker(
        "Upgrade candidates (up to 2)", "cu_trainees",
        fleet_filter=eligible_fleets,
        function_filter=eligible_functions,
        max_selections=2,
    )

    # Duration: compound for A330 FO → A320 CPT, else 1 month
    duration_hint = 1
    if to_fleet == "A320" and any(
        t.startswith("TBD") is False and
        any(p.employee_id == t and p.fleet == "A330" for p in ss.pilots)
        for t in trainees
    ):
        duration_hint = TRAINING_DURATIONS["a330_fo_to_a320_captain"]

    duration = st.number_input(
        "Duration (months) — 1 for same-fleet, 2 for A330 FO → A320 CPT",
        min_value=1, max_value=6, value=duration_hint, key="cu_dur",
    )

    instructor = ""
    if mode == "Internal":
        inst_list = _pilot_picker(
            "Instructor (destination fleet Captain)", "cu_instructor",
            fleet_filter=[to_fleet], function_filter=["Captain"],
            max_selections=1,
        )
        instructor = inst_list[0] if inst_list else ""

    note = st.text_input("Note (optional)", key="cu_note")

    if st.form_submit_button("Add Command Upgrade", type="primary"):
        if not trainees:
            st.error("Pick at least one candidate.")
        else:
            # from_fleet is inferred from the first trainee
            from_fleet = ""
            from_function = "First Officer"
            if trainees:
                t0 = trainees[0]
                if not t0.startswith("TBD"):
                    p0 = next((p for p in ss.pilots if p.employee_id == t0), None)
                    if p0:
                        from_fleet = p0.fleet
                        from_function = p0.function

            ss.actions.append(PlannedAction(
                id=new_id("act"), action_type="Command Upgrade",
                start_month=start, duration=duration, mode=mode,
                instructor_id=instructor, trainee_ids=trainees,
                from_fleet=from_fleet, from_function=from_function,
                to_fleet=to_fleet, to_function="Captain",
                note=note,
            ))
            st.success("Command Upgrade added.")
            st.rerun()


def _form_hire(d, kind: str):
    ss = st.session_state
    c1, c2, c3 = st.columns(3)
    with c1:
        name = st.text_input("New pilot name", key=f"hire_name_{kind}")
    with c2:
        if kind == "Cadet Hire":
            to_fleet = "ATR72"
            st.selectbox("To fleet", ["ATR72"], key=f"hire_to_fleet_{kind}",
                         disabled=True)
            to_function = "First Officer"
            st.selectbox("To function", ["First Officer"],
                         key=f"hire_to_func_{kind}", disabled=True)
        else:
            to_fleet = st.selectbox("To fleet", FLEETS, key=f"hire_to_fleet_{kind}")
            to_function = st.selectbox("To function", FUNCTIONS,
                                       key=f"hire_to_func_{kind}")
    with c3:
        start = _month_selector(d, f"hire_start_{kind}")

    if kind == "Cadet Hire":
        default_dur = TRAINING_DURATIONS["cadet_atr_fo"]
    else:
        default_dur = 0  # assume experienced hire, operational day 1
    duration = st.number_input(
        "Training lag (months before line-ready)",
        min_value=0, max_value=12, value=default_dur,
        key=f"hire_dur_{kind}",
    )
    note = st.text_input("Note (optional)", key=f"hire_note_{kind}")

    if st.form_submit_button(f"Add {kind}", type="primary"):
        if not name.strip():
            st.error("Name is required (use TBD if unknown).")
        else:
            nat = "Local" if kind in ("Cadet Hire", "Local Hire") else "Expat"
            ss.actions.append(PlannedAction(
                id=new_id("act"), action_type=kind,
                start_month=start, duration=duration, mode="—",
                to_fleet=to_fleet, to_function=to_function,
                new_pilot_name=name.strip(),
                new_pilot_nationality=nat,
                note=note,
            ))
            st.success(f"{kind} added.")
            st.rerun()


def _form_fleet_change(d):
    ss = st.session_state
    c1, c2, c3 = st.columns(3)
    with c1:
        fleet = st.selectbox("Fleet", FLEETS, key="fch_fleet")
    with c2:
        action = st.selectbox("Action", ["Acquire", "Dispose"], key="fch_action")
    with c3:
        start = _month_selector(d, "fch_start")
    note = st.text_input("Note", key="fch_note")
    if st.form_submit_button("Add Fleet Change", type="primary"):
        delta = 1 if action == "Acquire" else -1
        ss.fleet_changes.append(FleetChange(
            id=new_id("fc"), fleet=fleet,
            month_index=start, delta=delta, note=note,
        ))
        # Also record as a PlannedAction for the cascade catalogue
        ss.actions.append(PlannedAction(
            id=new_id("act"), action_type="Fleet Change",
            start_month=start, duration=0, mode="—",
            from_fleet=fleet, note=f"{action} 1× {fleet}. {note}".strip(),
        ))
        st.success(f"{action} 1× {fleet} scheduled.")
        st.rerun()


def _suggest_duration(action_type, from_fleet, from_func, to_fleet, to_func) -> int:
    if action_type == "Type Rating":
        if from_fleet == "DHC8" and to_fleet == "ATR72" and from_func == to_func:
            return TRAINING_DURATIONS["type_rating_dhc8_to_atr"]
        if to_fleet == "A320" and to_func == "First Officer" and from_fleet in ("ATR72", "DHC8"):
            return TRAINING_DURATIONS["type_rating_any_to_a320_fo"]
        if from_fleet == "A320" and from_func == "First Officer" and to_fleet == "A330" and to_func == "First Officer":
            return TRAINING_DURATIONS["type_rating_a320_fo_to_a330_fo"]
        return 2  # generic fallback
    return 1


# ---------------------------------------------------------------------------
# Cascade renderer (UI)
# ---------------------------------------------------------------------------
def _render_cascade_plot(graph, key_suffix: str = ""):
    nodes = graph["nodes"]
    edges = graph["edges"]
    if not nodes:
        st.info("No cascade to show.")
        return

    # Layout: x by depth via BFS from 'root', y stacked
    children: dict[str, list[str]] = {}
    for e in edges:
        children.setdefault(e["source"], []).append(e["target"])
    depth = {}
    root_ids = [n["id"] for n in nodes if n["id"] == "root"] or [nodes[0]["id"]]
    q = [(r, 0) for r in root_ids]
    while q:
        nid, dd = q.pop(0)
        if nid in depth and depth[nid] <= dd:
            continue
        depth[nid] = dd
        for c in children.get(nid, []):
            q.append((c, dd + 1))
    for n in nodes:
        depth.setdefault(n["id"], 0)

    by_depth: dict[int, list[str]] = {}
    for nid, dd in depth.items():
        by_depth.setdefault(dd, []).append(nid)

    pos: dict[str, tuple[float, float]] = {}
    for dd, ids in by_depth.items():
        for i, nid in enumerate(ids):
            pos[nid] = (dd * 2.6, (len(ids) - 1) / 2.0 - i)

    kind_color = {
        "trigger":  COLORS["accent"],
        "slot":     COLORS["amber"],
        "training": COLORS["blue"],
        "arrival":  COLORS["green"],
        "note":     COLORS["red"],
    }

    edge_x, edge_y, edge_text_x, edge_text_y, edge_texts = [], [], [], [], []
    for e in edges:
        if e["source"] not in pos or e["target"] not in pos:
            continue
        x0, y0 = pos[e["source"]]
        x1, y1 = pos[e["target"]]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]
        if e.get("label"):
            edge_text_x.append((x0 + x1) / 2)
            edge_text_y.append((y0 + y1) / 2)
            edge_texts.append(e["label"])

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=edge_x, y=edge_y, mode="lines",
        line=dict(color=COLORS["border"], width=1.6),
        hoverinfo="none", showlegend=False,
    ))
    if edge_texts:
        fig.add_trace(go.Scatter(
            x=edge_text_x, y=edge_text_y, mode="text",
            text=edge_texts,
            textfont=dict(size=10, color=COLORS["text_muted"]),
            hoverinfo="none", showlegend=False,
        ))

    for kind in ("trigger", "slot", "training", "arrival", "note"):
        xs, ys, texts, hovers = [], [], [], []
        for n in nodes:
            if n["kind"] != kind or n["id"] not in pos:
                continue
            x, y = pos[n["id"]]
            xs.append(x)
            ys.append(y)
            texts.append(n["label"].replace("\n", "<br>"))
            hovers.append(
                f"<b>{n['kind'].upper()}</b><br>{n['label']}"
                + (f"<br>Month index: {n['month']}" if n.get("month") is not None else "")
            )
        if not xs:
            continue
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="markers+text",
            marker=dict(size=36, color=kind_color[kind],
                        line=dict(color="white", width=2)),
            text=texts, textposition="middle right",
            textfont=dict(size=11, color=COLORS["text"]),
            name=kind.capitalize(),
            hovertext=hovers, hoverinfo="text",
        ))

    max_d = max(by_depth.keys()) if by_depth else 0
    fig.update_layout(
        height=340,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.2,
                    xanchor="center", x=0.5),
        xaxis=dict(visible=False, range=[-0.5, max_d * 2.6 + 3.5]),
        yaxis=dict(visible=False),
        margin=dict(l=10, r=10, t=10, b=40),
    )
    st.plotly_chart(fig, use_container_width=True,
                    key=f"cascade_{key_suffix}")


# ---------------------------------------------------------------------------
# TAB 6 — Localisation Tracker
# ---------------------------------------------------------------------------
def tab_localisation():
    ss = st.session_state
    d = derived()
    loc = d["loc"]

    section_header("Localisation overview")
    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("Overall local %", f"{loc['local_pct']:.1f}%")
    with c2: metric_card("Local pilots", loc["local"])
    with c3: metric_card("Expat pilots", loc["expat"])
    with c4: metric_card("Expats w/ feeder", _expats_with_feeder(ss.pilots))

    # Per-fleet progress bars
    section_header("Per-fleet local share")
    for f in FLEETS:
        v = loc["by_fleet"][f]
        pct = (v["local"] / v["total"] * 100) if v["total"] else 0.0
        c1, c2 = st.columns([1, 4])
        with c1:
            st.markdown(f"**{f}**")
            st.caption(f"{v['local']} / {v['total']} local")
        with c2:
            st.progress(min(1.0, pct / 100),
                        text=f"{pct:.0f}%")

    # Projected local percentage chart
    section_header("Projected local % over horizon (best case)")
    _render_localisation_projection(d)

    # Expat table
    section_header("Expat positions — next localisation candidates")
    expats = [p for p in ss.pilots if p.nationality == "Expat"]
    if not expats:
        info_panel("No expat positions in the registry.")
    else:
        rows = []
        for ex in expats:
            cands = eligible_feeders_for(ex, ss.pilots)
            best = cands[0] if cands else None
            rows.append({
                "Expat ID": ex.employee_id,
                "Expat Name": ex.full_name,
                "Position": f"{ex.fleet} {ex.function}",
                "Mgmt": "Yes" if ex.management else "No",
                "Best local candidate": best["pilot_name"] if best else "—",
                "Candidate from": best["from"] if best else "—",
                "Route": best["route"] if best else "No eligible local feeder",
                "Months": best["duration_months"] if best else "—",
                "Feasible": "✓" if best else "✗",
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True, height=360)

    # Recommended next actions
    section_header("Recommended next actions")
    recs = _recommended_localisation_actions(ss.pilots)
    if not recs:
        info_panel("No recommendations — either fully localised or no feeders available.")
    else:
        for r in recs[:6]:
            info_panel(
                f"👥 <b>{r['expat']}</b> ({r['position']}): "
                f"train <b>{r['candidate']}</b> via <i>{r['route']}</i> "
                f"in <b>{r['months']} months</b>.",
                kind="info",
            )


def _expats_with_feeder(pilots) -> int:
    n = 0
    for p in pilots:
        if p.nationality != "Expat":
            continue
        if eligible_feeders_for(p, pilots):
            n += 1
    return n


def _recommended_localisation_actions(pilots) -> list[dict]:
    recs = []
    used_candidates: set[str] = set()
    for ex in pilots:
        if ex.nationality != "Expat":
            continue
        cands = eligible_feeders_for(ex, pilots)
        for c in cands:
            if c["pilot_id"] in used_candidates:
                continue
            recs.append({
                "expat": ex.full_name,
                "position": f"{ex.fleet} {ex.function}",
                "candidate": c["pilot_name"],
                "route": c["route"],
                "months": c["duration_months"],
            })
            used_candidates.add(c["pilot_id"])
            break
    recs.sort(key=lambda r: r["months"])
    return recs


def _render_localisation_projection(d):
    """Project local % over horizon assuming every hire/type-rating lands as planned."""
    ss = st.session_state
    # Baseline: current registry
    baseline_local = d["loc"]["local"]
    baseline_total = d["loc"]["total"]

    # Walk through actions in chronological order, mutating a running state
    pct_series = []
    local = baseline_local
    total = baseline_total
    for m in range(ss.horizon):
        # Account for arrivals at this month
        for a in ss.actions:
            end = a.start_month + a.duration
            if end == m:
                if a.action_type in ("Cadet Hire", "Local Hire"):
                    local += 1
                    total += 1
                elif a.action_type == "Expat Hire":
                    total += 1
        # Fleet Change doesn't change pilot count in this projection
        pct = (local / total * 100) if total else 0.0
        pct_series.append(pct)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=d["labels"], y=pct_series,
        mode="lines+markers",
        line=dict(color=COLORS["accent"], width=3),
        marker=dict(size=6),
        fill="tozeroy",
        fillcolor="rgba(0,179,166,0.15)",
        name="Projected local %",
    ))
    fig.add_hline(y=100, line_dash="dash", line_color=COLORS["text_muted"],
                  annotation_text="Full localisation",
                  annotation_position="top left")
    fig.update_layout(
        height=300,
        yaxis=dict(range=[0, 105], title="Local %"),
        xaxis_title="Month", hovermode="x unified",
    )
    fig.update_xaxes(tickangle=-45)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# TAB 7 — Print Plan
# ---------------------------------------------------------------------------
def tab_print_plan():
    ss = st.session_state
    d = derived()

    section_header("Plan review")
    info_panel(
        "This tab gives you a final review before generating the PDF. "
        "The PDF contains: cover, executive summary, per-fleet breakdown, "
        "monthly requirement vs availability grid, full action list, "
        "cascade diagrams for every command upgrade, and the localisation roadmap.",
        kind="info",
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("Pilots", len(ss.pilots))
    with c2: metric_card("Aircraft", sum(ss.initial_aircraft.values()))
    with c3: metric_card("Actions", len(ss.actions))
    with c4: metric_card("Fleet changes", len(ss.fleet_changes))

    section_header("Action summary")
    if ss.actions:
        rows = []
        for a in sorted(ss.actions, key=lambda x: x.start_month):
            mo = d["labels"][a.start_month] if 0 <= a.start_month < len(d["labels"]) else f"M{a.start_month}"
            rows.append({
                "Month": mo,
                "Type": a.action_type,
                "From": f"{a.from_fleet} {a.from_function}".strip(),
                "To": f"{a.to_fleet} {a.to_function}".strip(),
                "Mode": a.mode,
                "Duration": f"{a.duration}mo",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        info_panel("No actions planned.")

    section_header("Generate PDF")
    cc1, cc2 = st.columns([1, 3])
    with cc1:
        if st.button("🖨 Generate PDF", type="primary", use_container_width=True):
            with st.spinner("Generating PDF…"):
                try:
                    pdf_bytes = build_pdf(current_state_payload())
                    st.session_state["pdf_bytes"] = pdf_bytes
                    st.success("PDF ready — download below.")
                except Exception as e:
                    st.error(f"PDF generation failed: {e}")

    if st.session_state.get("pdf_bytes"):
        st.download_button(
            "⬇ Download generated PDF",
            data=st.session_state["pdf_bytes"],
            file_name=f"iasl_crew_plan_{date.today().isoformat()}.pdf",
            mime="application/pdf",
            use_container_width=False,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    render_topbar()

    tabs = st.tabs([
        "📊 Dashboard",
        "👥 Registry",
        "✈ Fleet Planner",
        "📅 Timeline",
        "🎯 Action Planner",
        "🌏 Localisation",
        "🖨 Print Plan",
    ])

    with tabs[0]:
        tab_dashboard()
    with tabs[1]:
        tab_registry()
    with tabs[2]:
        tab_fleet_planner()
    with tabs[3]:
        tab_timeline()
    with tabs[4]:
        tab_action_planner()
    with tabs[5]:
        tab_localisation()
    with tabs[6]:
        tab_print_plan()


if __name__ == "__main__":
    main()
