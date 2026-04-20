"""
IASL Crew Planning Portal — main Streamlit entry point (consolidated).
"""

from __future__ import annotations

import hashlib
import io as _io
import json
from datetime import date, datetime

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
    COLORS, FLEET_COLORS,
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
                designations=[], management=False, status="Active",
            ))

    add("A330", "Captain", 2, 5, 1, ["TRI"])
    add("A330", "First Officer", 3, 4)
    add("A320", "Captain", 3, 2, 1, ["TRE"])
    add("A320", "First Officer", 4, 1)
    add("ATR72", "Captain", 22, 8, 2, ["TRE", "LI"])
    add("ATR72", "First Officer", 26, 4)
    add("DHC8", "Captain", 10, 5)
    add("DHC8", "First Officer", 12, 3)
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
    ss.fleet_changes = []
    ss.actions = []
    ss.initialised = True


_init_state()


def _autosave():
    """Keep a serialised backup of the full plan in session state."""
    try:
        st.session_state["_autosave_payload"] = serialise_state(current_state_payload())
    except Exception:
        pass


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


def derived():
    ss = st.session_state
    labels = month_labels(ss.start_year, ss.start_month, ss.horizon)
    ac_counts = resolve_aircraft_counts(ss.initial_aircraft, ss.fleet_changes, ss.horizon)
    req = fleet_requirement(ac_counts)
    avail = compute_availability(ss.pilots, ss.actions, ss.horizon)
    gaps = compute_gaps(req, avail)
    conflicts = detect_conflicts(ss.actions)
    loc = localisation_summary(ss.pilots)
    return {"labels": labels, "ac_counts": ac_counts, "req": req,
            "avail": avail, "gaps": gaps, "conflicts": conflicts, "loc": loc}


def _safe_df(rows: list[dict] | pd.DataFrame) -> pd.DataFrame:
    """Convert rows to a DataFrame with all string columns — Arrow-safe."""
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    return df.astype(str)


# ---------------------------------------------------------------------------
# Top navigation bar
# ---------------------------------------------------------------------------
def render_topbar():
    ss = st.session_state
    d = derived()
    total_pilots = len(ss.pilots)
    total_aircraft = sum(ss.initial_aircraft.values())
    period = f"{d['labels'][0]} → {d['labels'][-1]}" if d["labels"] else "—"

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
            width="stretch",
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

        if st.session_state.get("_autosave_payload"):
            if st.button("↩ Restore autosave", key="restore_autosave"):
                try:
                    restored = deserialise_state(st.session_state["_autosave_payload"])
                    for k, v in restored.items():
                        st.session_state[k] = v
                    st.success("Autosave restored.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Restore failed: {e}")

    with c3:
        if st.button("🖨 Print PDF", width="stretch", type="primary"):
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
    with c1: metric_card("Total pilots", len(ss.pilots))
    with c2: metric_card("Total aircraft", sum(ss.initial_aircraft.values()))
    with c3: metric_card("Localisation", f"{d['loc']['local_pct']:.0f}%",
                         f"{d['loc']['local']} local · {d['loc']['expat']} expat")
    with c4: metric_card("Planned actions", len(ss.actions))
    with c5:
        critical = sum(
            1 for f in FLEETS for fn in FUNCTIONS for m in range(ss.horizon)
            if gap_band(d["gaps"][f][fn][m]) == "red"
        )
        metric_card("Red-band cells", critical, "across all fleets & months")

    section_header("Fleet status (month 1)")
    cols = st.columns(4)
    for i, f in enumerate(FLEETS):
        with cols[i]:
            cap_req = d["req"][f]["Captain"][0]
            fo_req  = d["req"][f]["First Officer"][0]
            cap_av  = d["avail"][f]["Captain"][0]
            fo_av   = d["avail"][f]["First Officer"][0]
            worst = max(d["gaps"][f]["Captain"][0], d["gaps"][f]["First Officer"][0])
            band = gap_band(worst)
            fleet_card(f, cap_req + fo_req, cap_av + fo_av,
                       d["ac_counts"][f][0], band)

    section_header("Warnings & conflicts")
    any_warn = False
    for c in d["conflicts"]:
        info_panel(f"⚠ <b>Conflict:</b> {c['reason']}", kind="warn")
        any_warn = True

    red_warnings = []
    for f in FLEETS:
        for fn in FUNCTIONS:
            for m in range(ss.horizon):
                if gap_band(d["gaps"][f][fn][m]) == "red":
                    red_warnings.append(f"{d['labels'][m]} — {f} {fn}: short {d['gaps'][f][fn][m]:.1f}")
                    break
    if red_warnings:
        info_panel("🔴 <b>Red-band shortfalls:</b><br>" + "<br>".join(red_warnings[:8])
                   + ("<br>…" if len(red_warnings) > 8 else ""), kind="error")
        any_warn = True

    tbd_count = sum(1 for a in ss.actions for t in a.trainee_ids if t.startswith("TBD"))
    if tbd_count:
        info_panel(f"ℹ {tbd_count} TBD trainee slot(s) across all planned actions.", kind="info")
        any_warn = True

    if not any_warn:
        info_panel("✓ No warnings. Plan looks clean.", kind="info")

    section_header("Gap heatmap across the planning horizon")
    _render_gap_heatmap(d)


def _render_gap_heatmap(d):
    rows: list[str] = []
    z: list[list[float]] = []
    texts: list[list[str]] = []
    for f in FLEETS:
        for fn in FUNCTIONS:
            rows.append(f"{f} · {fn[:3]}")
            rv, rt = [], []
            for m in range(len(d["labels"])):
                g = d["gaps"][f][fn][m]
                rv.append(0 if g < 1 else (1 if g < 2 else 2))
                rt.append(
                    f"{f} {fn}<br>{d['labels'][m]}<br>"
                    f"Req {d['req'][f][fn][m]} · Avl {d['avail'][f][fn][m]:.1f}<br>Gap {g:.1f}"
                )
            z.append(rv)
            texts.append(rt)

    fig = go.Figure(data=go.Heatmap(
        z=z, x=d["labels"], y=rows,
        colorscale=[[0.0, COLORS["green"]], [0.5, COLORS["amber"]], [1.0, COLORS["red"]]],
        zmin=0, zmax=2, showscale=False,
        text=texts, hoverinfo="text", xgap=2, ygap=3,
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

    total = len(ss.pilots)
    active = sum(1 for p in ss.pilots if p.status == "Active")
    mgmt = sum(1 for p in ss.pilots if p.management)
    local = sum(1 for p in ss.pilots if p.nationality == "Local")
    expat = sum(1 for p in ss.pilots if p.nationality == "Expat")

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: metric_card("Total", total)
    with c2: metric_card("Active", active)
    with c3: metric_card("Management", mgmt, "count 0.5 each")
    with c4: metric_card("Local", local)
    with c5: metric_card("Expat", expat)

    section_header("Filter")
    f1, f2, f3, f4, f5 = st.columns(5)
    with f1: f_fleet = st.multiselect("Fleet", FLEETS, default=FLEETS, key="reg_f_fleet")
    with f2: f_func = st.multiselect("Function", FUNCTIONS, default=FUNCTIONS, key="reg_f_func")
    with f3: f_nat = st.multiselect("Nationality", NATIONALITIES, default=NATIONALITIES, key="reg_f_nat")
    with f4: f_status = st.multiselect("Status", PILOT_STATUSES, default=PILOT_STATUSES, key="reg_f_status")
    with f5: f_mgmt = st.selectbox("Management", ["All", "Yes", "No"], key="reg_f_mgmt")

    def _match(p):
        if p.fleet not in f_fleet: return False
        if p.function not in f_func: return False
        if p.nationality not in f_nat: return False
        if p.status not in f_status: return False
        if f_mgmt == "Yes" and not p.management: return False
        if f_mgmt == "No" and p.management: return False
        return True

    filtered = [p for p in ss.pilots if _match(p)]

    section_header(f"Pilots ({len(filtered)})")
    if filtered:
        df = pd.DataFrame([{
            "ID": p.employee_id, "Name": p.full_name,
            "Fleet": p.fleet, "Function": p.function,
            "Nationality": p.nationality,
            "Designations": ", ".join(p.designations) if p.designations else "—",
            "Mgmt": "Yes" if p.management else "No",
            "Weight": f"{p.contribution():.1f}", "Status": p.status,
        } for p in filtered])
        st.dataframe(_safe_df(df), hide_index=True, height=360, width="stretch")
    else:
        info_panel("No pilots match the current filters.")

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
            if st.form_submit_button("Add pilot", type="primary"):
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
            key = st.selectbox("Select pilot", list(options.keys()))
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
                    new_desig = st.multiselect("Designations", DESIGNATIONS, default=p.designations)
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
            key = st.selectbox("Select pilot to delete", list(options.keys()), key="del_pilot_sel")
            if st.button("Delete pilot", type="primary"):
                ss.pilots = [x for x in ss.pilots if x.employee_id != options[key].employee_id]
                st.success(f"Deleted {options[key].full_name}.")
                st.rerun()

    with tab_csv:
        _csv_import_export()


def _csv_import_export():
    ss = st.session_state
    st.markdown("**Export** all pilots as CSV:")
    if ss.pilots:
        df = pd.DataFrame([{
            "employee_id": p.employee_id, "full_name": p.full_name,
            "nationality": p.nationality, "fleet": p.fleet, "function": p.function,
            "designations": "|".join(p.designations),
            "management": p.management, "status": p.status,
        } for p in ss.pilots])
        st.download_button("Download CSV", data=df.to_csv(index=False),
                           file_name="iasl_pilots.csv", mime="text/csv")

    st.markdown("---")
    st.markdown("**Import** pilots from CSV")
    st.caption(
        "Required columns: employee_id, full_name, nationality, fleet, function. "
        "Optional: designations (pipe-separated), management (true/false), status."
    )

    template_df = pd.DataFrame([
        {"employee_id": "P001", "full_name": "Example Captain",
         "nationality": "Local", "fleet": "ATR72", "function": "Captain",
         "designations": "TRE|LI", "management": False, "status": "Active"},
    ])
    st.download_button("📄 Download CSV template",
                       data=template_df.to_csv(index=False),
                       file_name="iasl_pilots_template.csv", mime="text/csv")

    replace_mode = st.checkbox("Replace existing registry", value=False, key="csv_replace_mode")

    up = st.file_uploader(
        "Upload CSV", type=["csv"],
        key=f"csv_up_{st.session_state.get('csv_upload_counter', 0)}",
    )

    if up is not None:
        try:
            df = pd.read_csv(up, dtype=str, keep_default_na=False)
            df.columns = [c.strip().lower() for c in df.columns]
            required = {"employee_id", "full_name", "nationality", "fleet", "function"}
            missing = required - set(df.columns)
            if missing:
                st.error(f"CSV is missing required columns: {', '.join(sorted(missing))}")
                return

            errors, new_pilots = [], []
            for idx, r in df.iterrows():
                row_num = idx + 2
                eid = str(r["employee_id"]).strip()
                name = str(r["full_name"]).strip()
                nat = str(r["nationality"]).strip()
                fleet = str(r["fleet"]).strip()
                func = str(r["function"]).strip()

                if not eid:  errors.append(f"Row {row_num}: empty employee_id"); continue
                if not name: errors.append(f"Row {row_num}: empty full_name"); continue
                if nat not in set(NATIONALITIES):
                    errors.append(f"Row {row_num}: nationality '{nat}' invalid"); continue
                if fleet not in set(FLEETS):
                    errors.append(f"Row {row_num}: fleet '{fleet}' invalid"); continue
                if func not in set(FUNCTIONS):
                    errors.append(f"Row {row_num}: function '{func}' invalid"); continue

                desig_raw = str(r.get("designations", "")).strip()
                desigs = [d.strip() for d in desig_raw.split("|") if d.strip()]
                mgmt_raw = str(r.get("management", "")).strip().lower()
                mgmt = mgmt_raw in ("true", "1", "yes", "y", "t")
                status = str(r.get("status", "Active")).strip() or "Active"
                if status not in set(PILOT_STATUSES):
                    errors.append(f"Row {row_num}: status '{status}' invalid"); continue

                new_pilots.append(Pilot(
                    employee_id=eid, full_name=name, nationality=nat,
                    fleet=fleet, function=func,
                    designations=desigs, management=mgmt, status=status,
                ))

            st.markdown(f"**Parsed {len(new_pilots)} valid row(s) from {len(df)} total.**")
            if errors:
                with st.expander(f"⚠ {len(errors)} row(s) had errors"):
                    for e in errors[:50]: st.markdown(f"- {e}")

            if new_pilots:
                preview = pd.DataFrame([{
                    "ID": p.employee_id, "Name": p.full_name,
                    "Fleet": p.fleet, "Function": p.function,
                    "Nationality": p.nationality,
                    "Mgmt": "Yes" if p.management else "No", "Status": p.status,
                } for p in new_pilots[:20]])
                st.markdown("**Preview (first 20 rows):**")
                st.dataframe(_safe_df(preview), hide_index=True, width="stretch")

                if st.button(f"✓ Confirm import of {len(new_pilots)} pilot(s)",
                             type="primary", key="csv_commit"):
                    if replace_mode: ss.pilots = []
                    existing_ids = {p.employee_id for p in ss.pilots}
                    added = skipped = 0
                    for p in new_pilots:
                        if p.employee_id in existing_ids:
                            skipped += 1; continue
                        ss.pilots.append(p)
                        existing_ids.add(p.employee_id)
                        added += 1
                    msg = f"Imported {added} pilot(s)."
                    if skipped: msg += f" Skipped {skipped} duplicate ID(s)."
                    st.success(msg)
                    st.session_state["csv_upload_counter"] = st.session_state.get("csv_upload_counter", 0) + 1
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
    cols = st.columns(4)
    for i, f in enumerate(FLEETS):
        with cols[i]:
            v = st.number_input(f, min_value=0, max_value=30,
                                value=ss.initial_aircraft[f], key=f"init_ac_{f}")
            ss.initial_aircraft[f] = v

    section_header("Planning window")
    h1, h2, h3 = st.columns(3)
    with h1:
        ss.start_year = st.number_input("Start year", min_value=2024, max_value=2040,
                                        value=ss.start_year, key="sy")
    with h2:
        ss.start_month = st.number_input("Start month", min_value=1, max_value=12,
                                         value=ss.start_month, key="sm")
    with h3:
        ss.horizon = st.slider("Horizon (months)", min_value=6, max_value=60,
                               value=ss.horizon, key="hz")

    section_header("Add fleet change")
    with st.form("add_fleet_change", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([1, 1, 2, 2])
        with c1: fc_fleet = st.selectbox("Fleet", FLEETS, key="fc_fleet")
        with c2: fc_action = st.selectbox("Action", ["Acquire", "Dispose"], key="fc_action")
        with c3:
            fc_month = st.selectbox(
                "Month",
                options=list(range(ss.horizon)),
                format_func=lambda i: f"{i+1:2d}. {month_index_to_label(ss.start_year, ss.start_month, i)}",
                key="fc_month",
            )
        with c4: fc_note = st.text_input("Note (optional)", key="fc_note")
        if st.form_submit_button("Add fleet change", type="primary"):
            delta = 1 if fc_action == "Acquire" else -1
            ss.fleet_changes.append(FleetChange(
                id=new_id("fc"), fleet=fc_fleet,
                month_index=fc_month, delta=delta, note=fc_note,
            ))
            st.success(f"{fc_action} 1× {fc_fleet} at month {fc_month+1}.")
            st.rerun()

    if ss.fleet_changes:
        section_header("Scheduled fleet changes")
        for c in sorted(ss.fleet_changes, key=lambda x: x.month_index):
            cc1, cc2 = st.columns([10, 1])
            with cc1:
                verb = "Acquire" if c.delta > 0 else "Dispose"
                color = "green" if c.delta > 0 else "amber"
                st.markdown(
                    f"{pill(verb, color)} &nbsp; <b>{c.fleet}</b> &nbsp; at &nbsp; "
                    f"<b>{month_index_to_label(ss.start_year, ss.start_month, c.month_index)}</b>"
                    f" &nbsp; <span style='color:{COLORS['text_muted']}'>{c.note}</span>",
                    unsafe_allow_html=True,
                )
            with cc2:
                if st.button("✕", key=f"del_fc_{c.id}"):
                    ss.fleet_changes = [x for x in ss.fleet_changes if x.id != c.id]
                    st.rerun()

    section_header("Monthly aircraft count")
    rows = []
    for f in FLEETS:
        rows.append([f] + d["ac_counts"][f])
    df = pd.DataFrame(rows, columns=["Fleet"] + d["labels"])
    st.dataframe(_safe_df(df), hide_index=True, height=200, width="stretch")

    fig = go.Figure()
    for f in FLEETS:
        fig.add_trace(go.Scatter(
            x=d["labels"], y=d["ac_counts"][f],
            name=f, mode="lines+markers",
            line=dict(color=FLEET_COLORS[f], width=2.5),
            marker=dict(size=6),
        ))
    fig.update_layout(height=320, xaxis_title="Month", yaxis_title="Aircraft",
                      hovermode="x unified")
    fig.update_xaxes(tickangle=-45)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# TAB 4 — Timeline (multi-view, multi-fleet, shaded by function)
# ---------------------------------------------------------------------------
def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _fleet_function_color(fleet: str, function: str, alpha: float = 1.0) -> str:
    base = FLEET_COLORS[fleet]
    r, g, b = _hex_to_rgb(base)
    if function == "Captain":
        r, g, b = int(r * 0.65), int(g * 0.65), int(b * 0.65)
    else:
        r = int(r + (255 - r) * 0.45)
        g = int(g + (255 - g) * 0.45)
        b = int(b + (255 - b) * 0.45)
    return f"rgba({r},{g},{b},{alpha})"


def _add_action_markers(fig, d, fleets):
    ss = st.session_state
    acts = [a for a in ss.actions if a.from_fleet in fleets or a.to_fleet in fleets]
    for a in acts:
        if a.start_month < 0 or a.start_month >= len(d["labels"]): continue
        start_lbl = d["labels"][a.start_month]
        end_idx = min(a.start_month + max(1, a.duration), len(d["labels"]) - 1)
        end_lbl = d["labels"][end_idx]
        fig.add_vrect(
            x0=start_lbl, x1=end_lbl,
            fillcolor=COLORS["accent"], opacity=0.08,
            layer="below", line_width=0,
            annotation_text=a.action_type[:3],
            annotation_position="top left",
            annotation_font_size=9, annotation_font_color=COLORS["accent"],
        )


def tab_timeline():
    ss = st.session_state
    d = derived()

    section_header("Planning Timeline")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_fleets = st.multiselect("Fleets", FLEETS, default=FLEETS, key="tl_fleets")
    with c2:
        sel_funcs = st.multiselect("Functions", FUNCTIONS, default=FUNCTIONS, key="tl_funcs")
    with c3:
        view_mode = st.selectbox(
            "View",
            ["Requirement vs Availability", "Gap (shortfall)",
             "Nationality split", "Management vs line pilots"],
            key="tl_view",
        )
    with c4:
        show_actions = st.checkbox("Mark planned actions", value=True, key="tl_show_actions")

    if not sel_fleets or not sel_funcs:
        info_panel("Select at least one fleet and one function."); return

    if view_mode == "Requirement vs Availability":
        _tl_req_vs_avail(d, sel_fleets, sel_funcs, show_actions)
    elif view_mode == "Gap (shortfall)":
        _tl_gap(d, sel_fleets, sel_funcs, show_actions)
    elif view_mode == "Nationality split":
        _tl_nationality(d, sel_fleets, sel_funcs)
    elif view_mode == "Management vs line pilots":
        _tl_management(d, sel_fleets, sel_funcs)

    section_header("Planned actions in view")
    rel = [a for a in ss.actions if a.from_fleet in sel_fleets or a.to_fleet in sel_fleets]
    if not rel:
        info_panel("No planned actions touch the selected fleets.")
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
                "Type": a.action_type, "Detail": detail,
                "Duration": f"{a.duration}mo", "Mode": a.mode,
                "Trainees": ", ".join(a.trainee_ids) if a.trainee_ids else "—",
            })
        st.dataframe(_safe_df(rows), hide_index=True, width="stretch")

    section_header("Full grid (all fleets, all functions)")
    grid_rows = []
    for f in FLEETS:
        for fn in FUNCTIONS:
            row = {"Fleet": f, "Function": fn}
            for i, lbl in enumerate(d["labels"]):
                row[lbl] = f"{d['req'][f][fn][i]}/{d['avail'][f][fn][i]:.1f}"
            grid_rows.append(row)
    st.dataframe(_safe_df(grid_rows), hide_index=True, height=360, width="stretch")


def _tl_req_vs_avail(d, fleets, funcs, show_actions):
    fig = go.Figure()
    for f in fleets:
        for fn in funcs:
            col = _fleet_function_color(f, fn)
            col_light = _fleet_function_color(f, fn, 0.25)
            fig.add_trace(go.Scatter(
                x=d["labels"], y=d["req"][f][fn],
                name=f"{f} {fn} — required", mode="lines",
                line=dict(dash="dash", width=2, color=col),
                hovertemplate=f"<b>{f} {fn}</b><br>%{{x}}<br>Required: %{{y}}<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=d["labels"], y=d["avail"][f][fn],
                name=f"{f} {fn} — available", mode="lines+markers",
                line=dict(width=2.5, color=col), marker=dict(size=5, color=col),
                fill="tozeroy", fillcolor=col_light,
                hovertemplate=f"<b>{f} {fn}</b><br>%{{x}}<br>Available: %{{y:.1f}}<extra></extra>",
            ))
    if show_actions: _add_action_markers(fig, d, fleets)
    fig.update_layout(height=460, hovermode="x unified",
                      xaxis_title="Month", yaxis_title="Pilots",
                      legend=dict(orientation="h", yanchor="bottom", y=-0.35,
                                  xanchor="center", x=0.5, font=dict(size=10)))
    fig.update_xaxes(tickangle=-45)
    st.plotly_chart(fig, use_container_width=True)


def _tl_gap(d, fleets, funcs, show_actions):
    fig = go.Figure()
    for f in fleets:
        for fn in funcs:
            fig.add_trace(go.Bar(
                x=d["labels"], y=d["gaps"][f][fn], name=f"{f} {fn}",
                marker=dict(color=_fleet_function_color(f, fn), line=dict(width=0)),
                hovertemplate=f"<b>{f} {fn}</b><br>%{{x}}<br>Gap: %{{y:.1f}}<extra></extra>",
            ))
    if show_actions: _add_action_markers(fig, d, fleets)
    fig.add_hline(y=1, line_dash="dot", line_color=COLORS["amber"])
    fig.add_hline(y=2, line_dash="dot", line_color=COLORS["red"])
    fig.update_layout(height=420, barmode="group", hovermode="x unified",
                      xaxis_title="Month", yaxis_title="Pilot shortfall",
                      legend=dict(orientation="h", yanchor="bottom", y=-0.3,
                                  xanchor="center", x=0.5, font=dict(size=10)))
    fig.update_xaxes(tickangle=-45)
    st.plotly_chart(fig, use_container_width=True)


def _tl_nationality(d, fleets, funcs):
    ss = st.session_state
    fig = go.Figure()
    cats, local_vals, expat_vals, cols_local, cols_expat = [], [], [], [], []
    for f in fleets:
        for fn in funcs:
            group = [p for p in ss.pilots if p.fleet == f and p.function == fn and p.status == "Active"]
            local_vals.append(sum(1 for p in group if p.nationality == "Local"))
            expat_vals.append(sum(1 for p in group if p.nationality == "Expat"))
            cats.append(f"{f}<br>{fn[:3]}")
            cols_local.append(_fleet_function_color(f, fn, 1.0))
            cols_expat.append(_fleet_function_color(f, fn, 0.4))
    fig.add_trace(go.Bar(x=cats, y=local_vals, name="Local",
                         marker=dict(color=cols_local),
                         text=local_vals, textposition="inside"))
    fig.add_trace(go.Bar(x=cats, y=expat_vals, name="Expat",
                         marker=dict(color=cols_expat,
                                     pattern=dict(shape="/", size=6, solidity=0.3)),
                         text=expat_vals, textposition="inside"))
    fig.update_layout(height=420, barmode="stack",
                      xaxis_title="Fleet × Function", yaxis_title="Active pilots",
                      legend=dict(orientation="h", yanchor="bottom", y=-0.25,
                                  xanchor="center", x=0.5))
    st.plotly_chart(fig, use_container_width=True)


def _tl_management(d, fleets, funcs):
    ss = st.session_state
    fig = go.Figure()
    cats, line_vals, mgmt_vals, eff_vals, cols = [], [], [], [], []
    for f in fleets:
        for fn in funcs:
            group = [p for p in ss.pilots if p.fleet == f and p.function == fn and p.status == "Active"]
            line = sum(1 for p in group if not p.management)
            mgmt = sum(1 for p in group if p.management)
            line_vals.append(line); mgmt_vals.append(mgmt)
            eff_vals.append(line + 0.5 * mgmt)
            cats.append(f"{f}<br>{fn[:3]}")
            cols.append(_fleet_function_color(f, fn))
    fig.add_trace(go.Bar(x=cats, y=line_vals, name="Line pilots (1.0)",
                         marker=dict(color=cols), text=line_vals, textposition="inside"))
    fig.add_trace(go.Bar(x=cats, y=mgmt_vals, name="Management (0.5)",
                         marker=dict(color=cols,
                                     pattern=dict(shape="x", size=6, solidity=0.3)),
                         text=mgmt_vals, textposition="inside"))
    fig.add_trace(go.Scatter(x=cats, y=eff_vals, name="Effective weight",
                             mode="markers+text",
                             marker=dict(size=14, color=COLORS["navy"], symbol="diamond"),
                             text=[f"{v:.1f}" for v in eff_vals],
                             textposition="top center"))
    fig.update_layout(height=460, barmode="stack",
                      xaxis_title="Fleet × Function", yaxis_title="Pilots",
                      legend=dict(orientation="h", yanchor="bottom", y=-0.25,
                                  xanchor="center", x=0.5))
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# TAB 5 — Action Planner
# ---------------------------------------------------------------------------
def tab_action_planner():
    ss = st.session_state
    d = derived()

    section_header("Add action")
    action_type = st.selectbox("Action type", ACTION_TYPES, key="new_action_type")

    with st.form("add_action", clear_on_submit=False):
        if action_type == "Type Rating":        _form_type_rating(d)
        elif action_type == "Command Upgrade":  _form_command_upgrade(d)
        elif action_type == "Cadet Hire":       _form_hire(d, "Cadet Hire")
        elif action_type == "Expat Hire":       _form_hire(d, "Expat Hire")
        elif action_type == "Local Hire":       _form_hire(d, "Local Hire")
        elif action_type == "Fleet Change":     _form_fleet_change(d)

    if d["conflicts"]:
        section_header("Conflicts")
        for c in d["conflicts"]:
            info_panel(f"⚠ {c['reason']}", kind="warn")

    section_header("Scheduled actions")
    if not ss.actions:
        info_panel("No actions yet."); return

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
                    f"**Duration:** {a.duration}mo  &nbsp; **Mode:** {a.mode}  &nbsp; "
                    f"**Instructor:** {a.instructor_id or '—'}  &nbsp; "
                    f"**Trainees:** {', '.join(a.trainee_ids) if a.trainee_ids else '—'}"
                )
                if a.note: st.markdown(f"_{a.note}_")
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


def _pilot_picker(label, key, fleet_filter=None, function_filter=None,
                  nationality_filter=None, allow_tbd=True, max_selections=None,
                  show_filter_toggle=True) -> list[str]:
    """Searchable all-pilot picker with optional eligibility-only filter."""
    ss = st.session_state
    pool = list(ss.pilots)

    filters_active = bool(fleet_filter or function_filter or nationality_filter)
    apply_filter = False
    if filters_active and show_filter_toggle:
        apply_filter = st.checkbox(
            "Show only eligible pilots for this action",
            value=False, key=key + "_filter_toggle",
        )

    if apply_filter:
        if fleet_filter:      pool = [p for p in pool if p.fleet in fleet_filter]
        if function_filter:   pool = [p for p in pool if p.function in function_filter]
        if nationality_filter: pool = [p for p in pool if p.nationality in nationality_filter]

    status_rank = {"Active": 0, "On Type Rating": 1, "On Leave": 2}
    pool.sort(key=lambda p: (status_rank.get(p.status, 3), p.fleet, p.function, p.full_name))

    options_map = {}
    for p in pool:
        mgmt_tag = " · MGMT" if p.management else ""
        status_tag = "" if p.status == "Active" else f" · {p.status}"
        lbl = f"{p.employee_id}  |  {p.full_name}  |  {p.fleet} {p.function}  |  {p.nationality}{mgmt_tag}{status_tag}"
        options_map[lbl] = p.employee_id

    if allow_tbd:
        options_map["TBD-1  (placeholder)"] = "TBD-1"
        options_map["TBD-2  (placeholder)"] = "TBD-2"

    selected = st.multiselect(label, list(options_map.keys()),
                              key=key, max_selections=max_selections,
                              placeholder="Type a name, ID, fleet, or function…")
    return [options_map[s] for s in selected]


def _form_type_rating(d):
    ss = st.session_state

    c1, c2, c3 = st.columns(3)
    with c1:
        from_fleet = st.selectbox("From fleet", FLEETS, key="tr_from_fleet")
    with c2:
        to_fleet = st.selectbox("To fleet", FLEETS, key="tr_to_fleet")
    with c3:
        mode = st.selectbox("Mode", ["External", "Internal"], key="tr_mode")

    c1, c2 = st.columns(2)
    with c1:
        start = _month_selector(d, "tr_start")
    with c2:
        # Duration suggestion — use the longer of the two same-function routes
        dur_cpt = _suggest_duration("Type Rating", from_fleet, "Captain", to_fleet, "Captain")
        dur_fo = _suggest_duration("Type Rating", from_fleet, "First Officer", to_fleet, "First Officer")
        suggested = max(dur_cpt, dur_fo)
        duration = st.number_input("Duration (months)", 1, 12, suggested, key="tr_dur")

    st.markdown("---")
    st.markdown(
        "**Select up to 2 trainees and choose each one's role.** "
        "Seat Support pilots are off line ops during the course but do NOT "
        "change fleet or function — useful when a course needs a second seat "
        "occupant who is not themselves transitioning."
    )

    # Trainee 1 -------------------------------------------------------------
    st.markdown("**Trainee 1**")
    c1, c2 = st.columns([3, 2])
    with c1:
        t1_list = _pilot_picker(
            "Pilot", "tr_t1_pilot",
            fleet_filter=[from_fleet],
            function_filter=["Captain", "First Officer"],
            max_selections=1,
        )
    with c2:
        t1_role = st.selectbox(
            "Role",
            ["Captain → Captain", "First Officer → First Officer", "Seat Support"],
            key="tr_t1_role",
        )

    # Trainee 2 -------------------------------------------------------------
    st.markdown("**Trainee 2** (optional)")
    c1, c2 = st.columns([3, 2])
    with c1:
        t2_list = _pilot_picker(
            "Pilot", "tr_t2_pilot",
            fleet_filter=[from_fleet],
            function_filter=["Captain", "First Officer"],
            max_selections=1,
        )
    with c2:
        t2_role = st.selectbox(
            "Role",
            ["— none —", "Captain → Captain",
             "First Officer → First Officer", "Seat Support"],
            key="tr_t2_role",
        )

    instructor = ""
    if mode == "Internal":
        st.markdown("**Instructor** (destination fleet Captain, off line ops for duration)")
        il = _pilot_picker(
            "Instructor", "tr_instructor",
            fleet_filter=[to_fleet], function_filter=["Captain"],
            max_selections=1,
        )
        instructor = il[0] if il else ""

    note = st.text_input("Note (optional)", key="tr_note")

    if st.form_submit_button("Add Type Rating", type="primary"):
        # Collect trainees with their roles
        candidates = []
        if t1_list:
            candidates.append((t1_list[0], t1_role))
        if t2_list and t2_role != "— none —":
            candidates.append((t2_list[0], t2_role))

        if not candidates:
            st.error("Pick at least one trainee.")
            return

        # Split into transitioning vs seat support
        transitioning_cpt: list[str] = []
        transitioning_fo: list[str] = []
        seat_support: list[str] = []
        for pid, role in candidates:
            if role == "Captain → Captain":
                transitioning_cpt.append(pid)
            elif role == "First Officer → First Officer":
                transitioning_fo.append(pid)
            elif role == "Seat Support":
                seat_support.append(pid)

        # Validate: transitioning pilots must match their role's origin function.
        # TBD placeholders bypass the check.
        pilot_by_id = {p.employee_id: p for p in ss.pilots}
        for pid in transitioning_cpt:
            if pid.startswith("TBD"):
                continue
            p = pilot_by_id.get(pid)
            if p and p.function != "Captain":
                st.error(
                    f"{p.full_name} is a First Officer — cannot assign Captain → Captain role. "
                    "Switch their role to First Officer → First Officer or Seat Support."
                )
                return
        for pid in transitioning_fo:
            if pid.startswith("TBD"):
                continue
            p = pilot_by_id.get(pid)
            if p and p.function != "First Officer":
                st.error(
                    f"{p.full_name} is a Captain — cannot assign First Officer → First Officer role. "
                    "Switch their role to Captain → Captain or Seat Support."
                )
                return

        # Emit actions. One PlannedAction per destination function so the
        # availability engine handles routing cleanly. Seat support pilots
        # are attached to whichever action is created (or a standalone one)
        # via a dedicated seat_support list encoded in trainee_ids with a
        # "SEAT:" prefix so they're removed from line ops but not routed.
        added = 0
        cohort_tag = new_id("grp")

        if transitioning_cpt:
            ss.actions.append(PlannedAction(
                id=new_id("act"), action_type="Type Rating",
                start_month=start, duration=duration, mode=mode,
                instructor_id=instructor, trainee_ids=list(transitioning_cpt),
                from_fleet=from_fleet, from_function="Captain",
                to_fleet=to_fleet, to_function="Captain",
                note=(note + f"  [cohort {cohort_tag}]").strip(),
            ))
            added += 1

        if transitioning_fo:
            ss.actions.append(PlannedAction(
                id=new_id("act"), action_type="Type Rating",
                start_month=start, duration=duration, mode=mode,
                # Avoid double-counting instructor if both CPT and FO actions are created
                instructor_id="" if transitioning_cpt else instructor,
                trainee_ids=list(transitioning_fo),
                from_fleet=from_fleet, from_function="First Officer",
                to_fleet=to_fleet, to_function="First Officer",
                note=(note + f"  [cohort {cohort_tag}]").strip(),
            ))
            added += 1

        # Seat-support-only action (no transitioning pilots)
        if seat_support and not (transitioning_cpt or transitioning_fo):
            ss.actions.append(PlannedAction(
                id=new_id("act"), action_type="Type Rating",
                start_month=start, duration=duration, mode=mode,
                instructor_id=instructor,
                trainee_ids=[f"SEAT:{pid}" for pid in seat_support],
                from_fleet=from_fleet, from_function="",
                to_fleet=to_fleet, to_function="",
                note=(note + f"  [seat support only, cohort {cohort_tag}]").strip(),
            ))
            added += 1
        elif seat_support:
            # Attach seat-support pilots to the first emitted action so their
            # unavailability is recorded without altering their destination.
            # Find the most recent cohort action and append SEAT: entries.
            for act in reversed(ss.actions):
                if f"cohort {cohort_tag}" in act.note:
                    act.trainee_ids = list(act.trainee_ids) + [f"SEAT:{pid}" for pid in seat_support]
                    break

        st.success(f"Added {added} Type Rating action(s).")
        st.rerun()


def _form_command_upgrade(d):
    ss = st.session_state
    c1, c2, c3 = st.columns(3)
    with c1: to_fleet = st.selectbox("To fleet (Captain)", FLEETS, key="cu_to_fleet")
    with c2: mode = st.selectbox("Mode", ["External", "Internal"], key="cu_mode")
    with c3: start = _month_selector(d, "cu_start")

    if to_fleet == "A330":
        eligible_fleets, eligible_functions = ["A330", "A320"], ["First Officer", "Captain"]
    elif to_fleet == "A320":
        eligible_fleets, eligible_functions = ["A320", "A330"], ["First Officer"]
    else:
        eligible_fleets, eligible_functions = [to_fleet], ["First Officer"]

    st.caption(f"Eligible pool for {to_fleet} Captain upgrade: "
               f"{', '.join(eligible_fleets)} · {', '.join(eligible_functions)}")

    trainees = _pilot_picker("Upgrade candidates (up to 2)", "cu_trainees",
                             fleet_filter=eligible_fleets,
                             function_filter=eligible_functions, max_selections=2)

    duration_hint = 1
    if to_fleet == "A320" and any(
        (not t.startswith("TBD")) and
        any(p.employee_id == t and p.fleet == "A330" for p in ss.pilots)
        for t in trainees
    ):
        duration_hint = TRAINING_DURATIONS["a330_fo_to_a320_captain"]

    duration = st.number_input("Duration (months)", 1, 6, duration_hint, key="cu_dur")

    instructor = ""
    if mode == "Internal":
        il = _pilot_picker("Instructor (destination fleet Captain)", "cu_instructor",
                           fleet_filter=[to_fleet], function_filter=["Captain"],
                           max_selections=1)
        instructor = il[0] if il else ""

    note = st.text_input("Note (optional)", key="cu_note")

    if st.form_submit_button("Add Command Upgrade", type="primary"):
        if not trainees:
            st.error("Pick at least one candidate.")
        else:
            from_fleet, from_function = "", "First Officer"
            t0 = trainees[0]
            if not t0.startswith("TBD"):
                p0 = next((p for p in ss.pilots if p.employee_id == t0), None)
                if p0: from_fleet = p0.fleet; from_function = p0.function

            ss.actions.append(PlannedAction(
                id=new_id("act"), action_type="Command Upgrade",
                start_month=start, duration=duration, mode=mode,
                instructor_id=instructor, trainee_ids=trainees,
                from_fleet=from_fleet, from_function=from_function,
                to_fleet=to_fleet, to_function="Captain", note=note,
            ))
            st.success("Command Upgrade added."); st.rerun()


def _form_hire(d, kind: str):
    ss = st.session_state
    c1, c2, c3 = st.columns(3)
    with c1: name = st.text_input("New pilot name", key=f"hire_name_{kind}")
    with c2:
        if kind == "Cadet Hire":
            to_fleet = "ATR72"
            st.selectbox("To fleet", ["ATR72"], key=f"hire_to_fleet_{kind}", disabled=True)
            to_function = "First Officer"
            st.selectbox("To function", ["First Officer"], key=f"hire_to_func_{kind}", disabled=True)
        else:
            to_fleet = st.selectbox("To fleet", FLEETS, key=f"hire_to_fleet_{kind}")
            to_function = st.selectbox("To function", FUNCTIONS, key=f"hire_to_func_{kind}")
    with c3: start = _month_selector(d, f"hire_start_{kind}")

    default_dur = TRAINING_DURATIONS["cadet_atr_fo"] if kind == "Cadet Hire" else 0
    duration = st.number_input("Training lag (months)", 0, 12, default_dur,
                               key=f"hire_dur_{kind}")
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
                new_pilot_nationality=nat, note=note,
            ))
            st.success(f"{kind} added."); st.rerun()


def _form_fleet_change(d):
    ss = st.session_state
    c1, c2, c3 = st.columns(3)
    with c1: fleet = st.selectbox("Fleet", FLEETS, key="fch_fleet")
    with c2: action = st.selectbox("Action", ["Acquire", "Dispose"], key="fch_action")
    with c3: start = _month_selector(d, "fch_start")
    note = st.text_input("Note", key="fch_note")
    if st.form_submit_button("Add Fleet Change", type="primary"):
        delta = 1 if action == "Acquire" else -1
        ss.fleet_changes.append(FleetChange(
            id=new_id("fc"), fleet=fleet, month_index=start, delta=delta, note=note,
        ))
        ss.actions.append(PlannedAction(
            id=new_id("act"), action_type="Fleet Change",
            start_month=start, duration=0, mode="—",
            from_fleet=fleet, note=f"{action} 1× {fleet}. {note}".strip(),
        ))
        st.success(f"{action} 1× {fleet} scheduled."); st.rerun()


def _suggest_duration(action_type, from_fleet, from_func, to_fleet, to_func) -> int:
    if action_type == "Type Rating":
        if from_fleet == "DHC8" and to_fleet == "ATR72" and from_func == to_func:
            return TRAINING_DURATIONS["type_rating_dhc8_to_atr"]
        if to_fleet == "A320" and to_func == "First Officer" and from_fleet in ("ATR72", "DHC8"):
            return TRAINING_DURATIONS["type_rating_any_to_a320_fo"]
        if (from_fleet == "A320" and from_func == "First Officer"
                and to_fleet == "A330" and to_func == "First Officer"):
            return TRAINING_DURATIONS["type_rating_a320_fo_to_a330_fo"]
        return 2
    return 1


def _render_cascade_plot(graph, key_suffix: str = ""):
    nodes, edges = graph["nodes"], graph["edges"]
    if not nodes:
        st.info("No cascade to show."); return

    children = {}
    for e in edges:
        children.setdefault(e["source"], []).append(e["target"])
    depth = {}
    root_ids = [n["id"] for n in nodes if n["id"] == "root"] or [nodes[0]["id"]]
    q = [(r, 0) for r in root_ids]
    while q:
        nid, dd = q.pop(0)
        if nid in depth and depth[nid] <= dd: continue
        depth[nid] = dd
        for c in children.get(nid, []): q.append((c, dd + 1))
    for n in nodes: depth.setdefault(n["id"], 0)

    by_depth = {}
    for nid, dd in depth.items(): by_depth.setdefault(dd, []).append(nid)

    pos = {}
    for dd, ids in by_depth.items():
        for i, nid in enumerate(ids):
            pos[nid] = (dd * 2.6, (len(ids) - 1) / 2.0 - i)

    kind_color = {
        "trigger": COLORS["accent"], "slot": COLORS["amber"],
        "training": COLORS["blue"], "arrival": COLORS["green"],
        "note": COLORS["red"],
    }

    edge_x, edge_y, etx, ety, et = [], [], [], [], []
    for e in edges:
        if e["source"] not in pos or e["target"] not in pos: continue
        x0, y0 = pos[e["source"]]; x1, y1 = pos[e["target"]]
        edge_x += [x0, x1, None]; edge_y += [y0, y1, None]
        if e.get("label"):
            etx.append((x0 + x1) / 2); ety.append((y0 + y1) / 2); et.append(e["label"])

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode="lines",
                             line=dict(color=COLORS["border"], width=1.6),
                             hoverinfo="none", showlegend=False))
    if et:
        fig.add_trace(go.Scatter(x=etx, y=ety, mode="text", text=et,
                                 textfont=dict(size=10, color=COLORS["text_muted"]),
                                 hoverinfo="none", showlegend=False))

    for kind in ("trigger", "slot", "training", "arrival", "note"):
        xs, ys, texts, hovers = [], [], [], []
        for n in nodes:
            if n["kind"] != kind or n["id"] not in pos: continue
            x, y = pos[n["id"]]
            xs.append(x); ys.append(y)
            texts.append(n["label"].replace("\n", "<br>"))
            hovers.append(f"<b>{n['kind'].upper()}</b><br>{n['label']}"
                          + (f"<br>Month index: {n['month']}" if n.get("month") is not None else ""))
        if not xs: continue
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="markers+text",
            marker=dict(size=36, color=kind_color[kind],
                        line=dict(color="white", width=2)),
            text=texts, textposition="middle right",
            textfont=dict(size=11, color=COLORS["text"]),
            name=kind.capitalize(), hovertext=hovers, hoverinfo="text",
        ))

    max_d = max(by_depth.keys()) if by_depth else 0
    fig.update_layout(
        height=340, showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.2,
                    xanchor="center", x=0.5),
        xaxis=dict(visible=False, range=[-0.5, max_d * 2.6 + 3.5]),
        yaxis=dict(visible=False), margin=dict(l=10, r=10, t=10, b=40),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"cascade_{key_suffix}")


# ---------------------------------------------------------------------------
# TAB 6 — Localisation
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

    section_header("Per-fleet local share")
    for f in FLEETS:
        v = loc["by_fleet"][f]
        pct = (v["local"] / v["total"] * 100) if v["total"] else 0.0
        c1, c2 = st.columns([1, 4])
        with c1:
            st.markdown(f"**{f}**"); st.caption(f"{v['local']} / {v['total']} local")
        with c2: st.progress(min(1.0, pct / 100), text=f"{pct:.0f}%")

    section_header("Projected local % over horizon (best case)")
    _render_localisation_projection(d)

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
                "Months": str(best["duration_months"]) if best else "—",
                "Feasible": "✓" if best else "✗",
            })
        st.dataframe(_safe_df(rows), hide_index=True, height=360, width="stretch")

    section_header("Recommended next actions")
    recs = _recommended_localisation_actions(ss.pilots)
    if not recs:
        info_panel("No recommendations.")
    else:
        for r in recs[:6]:
            info_panel(f"👥 <b>{r['expat']}</b> ({r['position']}): "
                       f"train <b>{r['candidate']}</b> via <i>{r['route']}</i> "
                       f"in <b>{r['months']} months</b>.", kind="info")


def _expats_with_feeder(pilots) -> int:
    return sum(1 for p in pilots
               if p.nationality == "Expat" and eligible_feeders_for(p, pilots))


def _recommended_localisation_actions(pilots) -> list[dict]:
    recs, used = [], set()
    for ex in pilots:
        if ex.nationality != "Expat": continue
        for c in eligible_feeders_for(ex, pilots):
            if c["pilot_id"] in used: continue
            recs.append({"expat": ex.full_name,
                         "position": f"{ex.fleet} {ex.function}",
                         "candidate": c["pilot_name"],
                         "route": c["route"],
                         "months": c["duration_months"]})
            used.add(c["pilot_id"])
            break
    recs.sort(key=lambda r: r["months"])
    return recs


def _render_localisation_projection(d):
    ss = st.session_state
    local, total = d["loc"]["local"], d["loc"]["total"]
    pct_series = []
    for m in range(ss.horizon):
        for a in ss.actions:
            end = a.start_month + a.duration
            if end == m:
                if a.action_type in ("Cadet Hire", "Local Hire"):
                    local += 1; total += 1
                elif a.action_type == "Expat Hire":
                    total += 1
        pct_series.append((local / total * 100) if total else 0.0)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d["labels"], y=pct_series,
                             mode="lines+markers",
                             line=dict(color=COLORS["accent"], width=3),
                             marker=dict(size=6),
                             fill="tozeroy", fillcolor="rgba(0,133,122,0.15)",
                             name="Projected local %"))
    fig.add_hline(y=100, line_dash="dash", line_color=COLORS["text_muted"])
    fig.update_layout(height=300, yaxis=dict(range=[0, 105], title="Local %"),
                      xaxis_title="Month", hovermode="x unified")
    fig.update_xaxes(tickangle=-45)
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# TAB 7 — AI Optimiser (refresh-aware)
# ---------------------------------------------------------------------------
def _state_fingerprint() -> str:
    from dataclasses import asdict as _asdict
    ss = st.session_state
    payload = {
        "start_year": ss.get("start_year"),
        "start_month": ss.get("start_month"),
        "horizon": ss.get("horizon"),
        "initial_aircraft": ss.get("initial_aircraft", {}),
        "pilots": [_asdict(p) for p in ss.get("pilots", [])],
        "fleet_changes": [_asdict(c) for c in ss.get("fleet_changes", [])],
        "actions": [_asdict(a) for a in ss.get("actions", [])],
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _build_optimiser_prompt(state, derived_data, objectives, extra_notes) -> str:
    labels = derived_data["labels"]
    pilots = state["pilots"]
    actions = state["actions"]
    fleet_changes = state["fleet_changes"]

    def pilot_line(p):
        designations = "|".join(p.designations) if p.designations else "—"
        mgmt = "MGMT(0.5x)" if p.management else "LINE(1.0x)"
        return (f"  - {p.employee_id}: {p.full_name} | {p.nationality} | "
                f"{p.fleet} {p.function} | {p.status} | {mgmt} | designations: {designations}")

    buf = _io.StringIO()
    buf.write(
        "You are an experienced airline crew planning optimiser. Your task is to "
        "produce a month-by-month training and hiring plan for Island Aviation "
        "Services Limited (IASL) that satisfies all operational constraints and "
        "the stated objectives.\n\n"
    )
    buf.write("=" * 70 + "\nFIXED OPERATIONAL RULES — DO NOT VIOLATE\n" + "=" * 70 + "\n\n")
    buf.write(
        "Crew set ratios (1 crew set = 1 Captain + 1 First Officer):\n"
        "  A330: 7 per aircraft | A320: 5 | ATR72: 6 | DHC-8: 5\n\n"
        "Management Pilot contribution: 0.5 (regular = 1.0). Training/Leave = 0.0.\n\n"
        "Training durations (months):\n"
        "  DHC-8 Cpt -> ATR Cpt: 2 | DHC-8 FO -> ATR FO: 2\n"
        "  ATR/DHC-8 any -> A320 FO: 2 | A320 FO -> A330 FO: 1\n"
        "  Same-fleet Command Upgrade: 1 | A330 FO -> A320 CPT: 2 (compound)\n"
        "  Cadet -> ATR FO: 2 (training lag)\n\n"
        "Command Upgrade eligibility:\n"
        "  A330 CPT: A330 FOs or A320 CPTs\n"
        "  A320 CPT: A320 FOs or A330 FOs (A330 FO path = compound 2mo)\n"
        "  ATR/DHC-8 CPT: same-fleet FOs only\n\n"
        "Internal mode: destination-fleet CPT as instructor AND off-line. Up to 2 trainees.\n"
        "External mode: up to 2 trainees, no instructor consumed.\n\n"
        "Cadets: ATR FO only. 2-month type rating.\n\n"
        "Gap bands: <1 green, 1-2 amber, 2+ red.\n\n"
    )

    buf.write("=" * 70 + "\nOBJECTIVES FOR THIS PLAN\n" + "=" * 70 + "\n\n")
    obj_lines = []
    if objectives["close_gaps"]:              obj_lines.append("- Close all requirement gaps within horizon.")
    if objectives["localise"]:                obj_lines.append("- Maximise localisation: replace expats with locals where feasible.")
    if objectives["phase_out_dhc8"]:          obj_lines.append("- Phase out DHC-8 fleet into ATR72 / A320 FO.")
    if objectives["minimise_external_cost"]:  obj_lines.append("- Prefer Internal training over External where feasible.")
    if objectives["avoid_conflicts"]:         obj_lines.append("- No named pilot on two overlapping actions.")
    if objectives["stagger_trainings"]:       obj_lines.append(
        f"- Max {objectives['max_concurrent_per_fleet']} concurrent trainings per fleet.")
    buf.write("\n".join(obj_lines) + "\n\n")

    buf.write("=" * 70 + "\nCURRENT STATE\n" + "=" * 70 + "\n\n")
    buf.write(f"Planning period: {labels[0]} to {labels[-1]} ({state['horizon']} months)\n\n")

    buf.write("Initial aircraft (month 1):\n")
    for f in FLEETS: buf.write(f"  {f}: {state['initial_aircraft'][f]}\n")
    buf.write("\n")

    if fleet_changes:
        buf.write("Scheduled fleet changes:\n")
        for c in sorted(fleet_changes, key=lambda x: x.month_index):
            verb = "ACQUIRE" if c.delta > 0 else "DISPOSE"
            mo = labels[c.month_index] if 0 <= c.month_index < len(labels) else f"M{c.month_index}"
            note = f" — {c.note}" if c.note else ""
            buf.write(f"  {mo}: {verb} 1x {c.fleet}{note}\n")
        buf.write("\n")

    buf.write("Month-by-month aircraft count:\n")
    buf.write("  Month          | " + " | ".join(FLEETS) + "\n")
    for i, lbl in enumerate(labels):
        row = f"  {lbl:14s} | " + " | ".join(f"{derived_data['ac_counts'][f][i]:>5d}" for f in FLEETS)
        buf.write(row + "\n")
    buf.write("\n")

    buf.write("PILOT REGISTRY:\n\n")
    for f in FLEETS:
        for fn in FUNCTIONS:
            group = [p for p in pilots if p.fleet == f and p.function == fn]
            if not group: continue
            locals_n = sum(1 for p in group if p.nationality == "Local")
            expats_n = sum(1 for p in group if p.nationality == "Expat")
            mgmt_n = sum(1 for p in group if p.management)
            buf.write(f"{f} {fn} — {len(group)} ({locals_n} Local, {expats_n} Expat, {mgmt_n} Mgmt):\n")
            for p in sorted(group, key=lambda x: (x.nationality, x.full_name)):
                buf.write(pilot_line(p) + "\n")
            buf.write("\n")

    buf.write("REQUIREMENT vs AVAILABILITY (pre-plan):\n\n")
    buf.write("  Fleet   Func   " + "  ".join(f"{lbl:>10s}" for lbl in labels) + "\n")
    for f in FLEETS:
        for fn in FUNCTIONS:
            cells = "  ".join(f"{derived_data['req'][f][fn][i]:>3d}/{derived_data['avail'][f][fn][i]:>5.1f}"
                              for i in range(len(labels)))
            buf.write(f"  {f:6s}  {fn[:3]:>4s}   {cells}\n")
    buf.write("\n")

    if actions:
        buf.write("ALREADY-PLANNED ACTIONS (treat as fixed):\n\n")
        for a in sorted(actions, key=lambda x: x.start_month):
            mo = labels[a.start_month] if 0 <= a.start_month < len(labels) else f"M{a.start_month}"
            buf.write(
                f"  {mo} | {a.action_type} | "
                f"from {a.from_fleet} {a.from_function} -> to {a.to_fleet} {a.to_function} | "
                f"{a.duration}mo | mode={a.mode} | instructor={a.instructor_id or '—'} | "
                f"trainees={','.join(a.trainee_ids) if a.trainee_ids else '—'} | {a.note}\n"
            )
        buf.write("\n")

    if extra_notes:
        buf.write("=" * 70 + "\nADDITIONAL INSTRUCTIONS\n" + "=" * 70 + "\n\n")
        buf.write(extra_notes + "\n\n")

    buf.write("=" * 70 + "\nREQUIRED OUTPUT FORMAT\n" + "=" * 70 + "\n\n")
    buf.write(
        "Return your plan as a numbered list of actions. For EACH action:\n\n"
        "Action N:\n"
        "  Type: <Type Rating | Command Upgrade | Cadet Hire | Expat Hire | Local Hire>\n"
        "  Start month: <YYYY-MMM>\n"
        "  Duration: <months>\n"
        "  Mode: <Internal | External | —>\n"
        "  From: <fleet> <function>\n"
        "  To: <fleet> <function>\n"
        "  Instructor: <Employee ID or TBD>\n"
        "  Trainees: <Employee IDs or TBD-1/TBD-2>\n"
        "  Rationale: <one line>\n\n"
        "After the list: (1) post-plan requirement-vs-availability summary, "
        "(2) risk assessment, (3) assumptions.\n"
        "Use real Employee IDs from the registry wherever possible. Respect all rules.\n"
    )
    return buf.getvalue()


def tab_ai_optimiser():
    ss = st.session_state
    d = derived()

    section_header("AI optimisation prompt builder")
    info_panel(
        "Generates a structured prompt you can paste into Claude, ChatGPT, or any "
        "capable LLM. The AI returns an optimised plan you then enter manually in "
        "the Action Planner. Nothing is sent anywhere from this app.",
        kind="info",
    )

    fingerprint = _state_fingerprint()
    last_fp = ss.get("ai_prompt_fingerprint")
    last_at = ss.get("ai_prompt_built_at")
    is_stale = (last_fp is not None and last_fp != fingerprint)

    if last_at is None:
        info_panel("No prompt generated yet. Configure objectives and click <b>Build prompt</b>.", kind="info")
    elif is_stale:
        info_panel(f"⚠ <b>Prompt is out of date.</b> Registry / actions / fleet changed "
                   f"since {last_at}. Click <b>Rebuild prompt</b> to refresh.", kind="warn")
    else:
        info_panel(f"✓ Prompt reflects current state (built {last_at}).", kind="info")

    section_header("Optimisation objectives")
    c1, c2 = st.columns(2)
    with c1:
        obj_gaps = st.checkbox("Close all requirement gaps", value=True, key="ai_obj_gaps")
        obj_localise = st.checkbox("Maximise localisation", value=True, key="ai_obj_localise")
        obj_dhc = st.checkbox("Phase out DHC-8", value=True, key="ai_obj_dhc")
    with c2:
        obj_intern = st.checkbox("Minimise external training", value=True, key="ai_obj_intern")
        obj_conflicts = st.checkbox("Avoid pilot conflicts", value=True, key="ai_obj_conflicts")
        obj_stagger = st.checkbox("Stagger trainings", value=True, key="ai_obj_stagger")

    max_concurrent = st.slider("Max concurrent trainings per fleet", 1, 6, 2, key="ai_max_concurrent")

    extra_notes = st.text_area(
        "Additional instructions (optional)",
        placeholder="e.g., 'Prioritise A330 CPT localisation first.'",
        height=100, key="ai_extra_notes",
    )

    section_header("Build prompt")
    b1, b2, b3 = st.columns([1.5, 1.5, 4])
    with b1:
        lbl = "🔄 Rebuild prompt" if ss.get("ai_prompt_text") else "🛠 Build prompt"
        build_clicked = st.button(lbl, type="primary", width="stretch")
    with b2:
        if st.button("🗑 Clear prompt", width="stretch"):
            for k in ("ai_prompt_text", "ai_prompt_fingerprint", "ai_prompt_built_at"):
                ss.pop(k, None)
            st.rerun()
    with b3:
        st.caption("Rebuild whenever registry, actions, or fleet plan change.")

    if build_clicked:
        try:
            prompt = _build_optimiser_prompt(
                state=current_state_payload(),
                derived_data=d,
                objectives={
                    "close_gaps": obj_gaps, "localise": obj_localise,
                    "phase_out_dhc8": obj_dhc, "minimise_external_cost": obj_intern,
                    "avoid_conflicts": obj_conflicts, "stagger_trainings": obj_stagger,
                    "max_concurrent_per_fleet": max_concurrent,
                },
                extra_notes=extra_notes.strip(),
            )
            ss["ai_prompt_text"] = prompt
            ss["ai_prompt_fingerprint"] = fingerprint
            ss["ai_prompt_built_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.rerun()
        except Exception as e:
            st.error(f"Prompt build failed: {type(e).__name__}: {e}")

    if ss.get("ai_prompt_text"):
        section_header("Generated prompt")
        st.caption(f"Built at {ss['ai_prompt_built_at']}  ·  "
                   f"{len(ss['ai_prompt_text']):,} characters  ·  "
                   f"≈ {len(ss['ai_prompt_text']) // 4:,} tokens")
        st.text_area("Copy this prompt:", value=ss["ai_prompt_text"], height=480,
                     key=f"ai_prompt_preview_{ss['ai_prompt_fingerprint']}")
        c1, c2 = st.columns([1, 5])
        with c1:
            st.download_button("⬇ Download .txt", data=ss["ai_prompt_text"],
                               file_name=f"iasl_ai_prompt_{date.today().isoformat()}.txt",
                               mime="text/plain", width="stretch")
        with c2:
            st.caption("Claude Opus or GPT-4/5-class models handle this best.")


# ---------------------------------------------------------------------------
# TAB 8 — Print Plan
# ---------------------------------------------------------------------------
def tab_print_plan():
    ss = st.session_state
    d = derived()

    section_header("Plan review")
    info_panel(
        "Review your plan, then generate the PDF. The PDF contains cover, "
        "executive summary, per-fleet breakdown, monthly grid, full action list, "
        "cascade diagrams, and localisation roadmap.", kind="info",
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
                "Month": mo, "Type": a.action_type,
                "From": f"{a.from_fleet} {a.from_function}".strip(),
                "To": f"{a.to_fleet} {a.to_function}".strip(),
                "Mode": a.mode, "Duration": f"{a.duration}mo",
            })
        st.dataframe(_safe_df(rows), hide_index=True, width="stretch")
    else:
        info_panel("No actions planned.")

    section_header("Generate PDF")
    cc1, cc2 = st.columns([1, 3])
    with cc1:
        if st.button("🖨 Generate PDF", type="primary", width="stretch"):
            with st.spinner("Generating PDF…"):
                try:
                    pdf_bytes = build_pdf(current_state_payload())
                    st.session_state["pdf_bytes"] = pdf_bytes
                    st.success("PDF ready — download below.")
                except Exception as e:
                    st.error(f"PDF generation failed: {e}")

    if st.session_state.get("pdf_bytes"):
        st.download_button("⬇ Download generated PDF",
                           data=st.session_state["pdf_bytes"],
                           file_name=f"iasl_crew_plan_{date.today().isoformat()}.pdf",
                           mime="application/pdf")


# ---------------------------------------------------------------------------
# TAB — Crew Flow Map (time-dependent Sankey + coverage bubble chart)
# ---------------------------------------------------------------------------
def tab_flow_map():
    ss = st.session_state
    d = derived()

    section_header("Crew Flow Map")
    info_panel(
        "Visualises how pilots move through the fleet network over your planning "
        "horizon. The <b>Sankey</b> shows who transitions from where to where. "
        "The <b>coverage bubble chart</b> shows requirement vs availability at a "
        "glance — bubble size is fleet size, colour is gap severity, position "
        "shows localisation and coverage. Both are fully interactive — hover any "
        "band or bubble to drill into it.",
        kind="info",
    )

    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        view = st.selectbox(
            "Visualisation",
            ["Sankey — pilot transitions", "Bubble chart — coverage over time",
             "Network — fleet interconnections"],
            key="fm_view",
        )
    with c2:
        snapshot_month = st.selectbox(
            "Snapshot at end of month",
            options=list(range(ss.horizon)),
            format_func=lambda i: f"{i+1:2d}. {d['labels'][i]}",
            index=ss.horizon - 1,
            key="fm_snapshot",
        )
    with c3:
        st.caption(
            "Use the snapshot selector to see the state of your plan at any "
            "point in the horizon. Dragging, zooming, and hover-to-drill all "
            "work on the visualisations below."
        )

    if view == "Sankey — pilot transitions":
        _flow_sankey(d, snapshot_month)
    elif view == "Bubble chart — coverage over time":
        _flow_bubble(d)
    else:
        _flow_network(d, snapshot_month)


def _flow_node_label(fleet: str, function: str, nationality: str) -> str:
    return f"{fleet} {function[:3]} · {nationality[:3]}"


def _flow_sankey(d, up_to_month: int):
    """
    Sankey of pilot flows from month 0 (current registry state) to end of the
    selected snapshot month, following planned actions.
    Nodes = fleet × function × nationality. Plus virtual "New Hire" on the left
    and "Departed" on the right (currently unused but placed for future use).
    """
    ss = st.session_state

    # Build node list — source side and destination side are identical, we duplicate
    # so Plotly Sankey can place them in columns.
    combos: list[tuple[str, str, str]] = []
    for f in FLEETS:
        for fn in FUNCTIONS:
            for nat in NATIONALITIES:
                combos.append((f, fn, nat))

    # Source node index = 0..N-1. Destination node index = N..2N-1. Then Hire node.
    N = len(combos)
    src_label_of = {combos[i]: i for i in range(N)}
    dst_label_of = {combos[i]: i + N for i in range(N)}
    hire_idx_local = 2 * N
    hire_idx_expat = 2 * N + 1

    labels = [_flow_node_label(*c) + "  (start)" for c in combos] + \
             [_flow_node_label(*c) + "  (end)" for c in combos] + \
             ["New Local Hire", "New Expat Hire"]

    # Node colours: fleet colour, darkened by function, lightened by nationality
    def node_color(combo):
        f, fn, nat = combo
        base = _fleet_function_color(f, fn, 0.9 if nat == "Local" else 0.55)
        return base
    node_colors = [node_color(c) for c in combos] + \
                  [node_color(c) for c in combos] + \
                  ["rgba(22,163,74,0.7)", "rgba(220,38,38,0.55)"]

    # Aggregate pilot flows ------------------------------------------------
    # Start: every pilot sits on their current fleet/function/nationality node.
    # Then for each action completed by up_to_month, move them to the new node.
    flows: dict[tuple[int, int], dict] = {}  # (src, dst) -> {"value": n, "desc": [...]}

    def bump(src, dst, desc):
        key = (src, dst)
        if key not in flows:
            flows[key] = {"value": 0, "desc": []}
        flows[key]["value"] += 1
        flows[key]["desc"].append(desc)

    pilot_by_id = {p.employee_id: p for p in ss.pilots}

    # Track each pilot's destination node (starts as source)
    pilot_dest: dict[str, tuple[str, str, str]] = {
        p.employee_id: (p.fleet, p.function, p.nationality)
        for p in ss.pilots if p.fleet in FLEETS
    }

    # Walk actions that complete on or before up_to_month
    for a in sorted(ss.actions, key=lambda x: x.start_month):
        end = a.start_month + a.duration
        if end > up_to_month + 1:
            continue

        if a.action_type == "Type Rating":
            for tid in a.trainee_ids:
                if tid.startswith("TBD"):
                    continue
                p = pilot_by_id.get(tid)
                if not p or p.fleet not in FLEETS:
                    continue
                cur = pilot_dest.get(tid, (p.fleet, p.function, p.nationality))
                new = (a.to_fleet, a.to_function, p.nationality)
                if cur != new:
                    pilot_dest[tid] = new

        elif a.action_type == "Command Upgrade":
            for tid in a.trainee_ids:
                if tid.startswith("TBD"):
                    continue
                p = pilot_by_id.get(tid)
                if not p or p.fleet not in FLEETS:
                    continue
                cur = pilot_dest.get(tid, (p.fleet, p.function, p.nationality))
                new = (a.to_fleet, "Captain", p.nationality)
                if cur != new:
                    pilot_dest[tid] = new

        elif a.action_type in ("Cadet Hire", "Local Hire"):
            # New local pilot arriving on destination
            if a.to_fleet in FLEETS and a.to_function in FUNCTIONS:
                bump(
                    hire_idx_local,
                    dst_label_of[(a.to_fleet, a.to_function, "Local")],
                    f"Hire: {a.new_pilot_name or 'TBD'}",
                )

        elif a.action_type == "Expat Hire":
            if a.to_fleet in FLEETS and a.to_function in FUNCTIONS:
                bump(
                    hire_idx_expat,
                    dst_label_of[(a.to_fleet, a.to_function, "Expat")],
                    f"Hire: {a.new_pilot_name or 'TBD'}",
                )

    # Now emit flows for every existing pilot from source -> final destination
    for pid, dest in pilot_dest.items():
        p = pilot_by_id.get(pid)
        if not p or p.fleet not in FLEETS:
            continue
        src_combo = (p.fleet, p.function, p.nationality)
        if src_combo not in src_label_of or dest not in [(*c,) for c in combos]:
            continue
        src = src_label_of[src_combo]
        dst = dst_label_of[dest]
        bump(src, dst, f"{p.employee_id} — {p.full_name}")

    if not flows:
        info_panel(
            "No flows to display — either no actions are planned or all planned "
            "actions fall after the snapshot month. Try moving the snapshot "
            "forward or adding more actions."
        )
        return

    sources = [k[0] for k in flows.keys()]
    targets = [k[1] for k in flows.keys()]
    values = [v["value"] for v in flows.values()]
    hovers = [
        f"<b>{labels[k[0]]} → {labels[k[1]]}</b><br>"
        f"Count: {v['value']}<br>"
        + "<br>".join(v["desc"][:8])
        + (f"<br>…and {len(v['desc']) - 8} more" if len(v["desc"]) > 8 else "")
        for k, v in flows.items()
    ]

    # Flow colour = source node colour, semi-transparent
    def _soft(c):
        if c.startswith("rgba"):
            parts = c[5:-1].split(",")
            return f"rgba({parts[0]},{parts[1]},{parts[2]},0.35)"
        return c
    link_colors = [_soft(node_colors[s]) for s in sources]

    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            pad=14, thickness=14,
            line=dict(color=COLORS["border"], width=0.5),
            label=labels,
            color=node_colors,
            hovertemplate="<b>%{label}</b><br>Total: %{value}<extra></extra>",
        ),
        link=dict(
            source=sources, target=targets, value=values,
            color=link_colors,
            customdata=hovers,
            hovertemplate="%{customdata}<extra></extra>",
        ),
    ))
    fig.update_layout(
        height=680,
        margin=dict(l=10, r=10, t=20, b=20),
        font=dict(size=11),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Read left to right: starting position → ending position at the chosen "
        "snapshot month. Band thickness = number of pilots. Hover any band for "
        "the exact pilots involved. Hover any node for the node total."
    )


def _flow_bubble(d):
    """
    Bubble chart: x = month, y = localisation % for each fleet × function,
    bubble size = number of pilots at that fleet × function, colour = gap severity.
    Animation frames walk through the horizon.
    """
    ss = st.session_state
    frames_data = []
    fleet_fn_pairs = [(f, fn) for f in FLEETS for fn in FUNCTIONS]

    # Precompute per-month nationality counts from registry + arrivals
    # For simplicity we only count current registry (no per-month nationality
    # accounting) — future arrivals are folded in at completion month.
    pilot_snapshots: dict[int, list] = {m: list(ss.pilots) for m in range(ss.horizon)}

    # Fold hires into later snapshots
    for m in range(ss.horizon):
        for a in ss.actions:
            end = a.start_month + a.duration
            if end <= m and a.action_type in ("Cadet Hire", "Local Hire", "Expat Hire"):
                nat = "Local" if a.action_type in ("Cadet Hire", "Local Hire") else "Expat"
                pilot_snapshots[m].append(
                    Pilot(
                        employee_id=f"_virtual_{a.id}",
                        full_name=a.new_pilot_name or "TBD",
                        nationality=nat,
                        fleet=a.to_fleet, function=a.to_function,
                        designations=[], management=False, status="Active",
                    )
                )

    rows = []
    for m in range(ss.horizon):
        snap = pilot_snapshots[m]
        for f, fn in fleet_fn_pairs:
            group = [p for p in snap if p.fleet == f and p.function == fn]
            if not group:
                continue
            total = len(group)
            local = sum(1 for p in group if p.nationality == "Local")
            local_pct = (local / total * 100) if total else 0
            req = d["req"][f][fn][m]
            av = d["avail"][f][fn][m]
            gap = max(0.0, req - av)
            rows.append({
                "month_idx": m,
                "month": d["labels"][m],
                "fleet_fn": f"{f} {fn[:3]}",
                "fleet": f,
                "local_pct": local_pct,
                "total": total,
                "req": req,
                "av": av,
                "gap": gap,
                "band": gap_band(gap),
            })

    if not rows:
        info_panel("No data to display.")
        return

    df = pd.DataFrame(rows)

    # Colour map per band
    band_color = {"green": COLORS["green"], "amber": COLORS["amber"], "red": COLORS["red"]}

    # Build the chart with a slider/animation
    fig = go.Figure()

    # One trace per month, all invisible except the last; slider controls which
    frames = []
    for m in range(ss.horizon):
        sub = df[df.month_idx == m]
        if sub.empty:
            continue
        frames.append(go.Frame(
            name=d["labels"][m],
            data=[go.Scatter(
                x=sub["local_pct"],
                y=sub["av"],
                mode="markers+text",
                marker=dict(
                    size=sub["total"] * 3 + 10,
                    color=[band_color[b] for b in sub["band"]],
                    line=dict(color=COLORS["navy"], width=1.5),
                    opacity=0.85,
                ),
                text=sub["fleet_fn"],
                textposition="middle center",
                textfont=dict(size=9, color="white"),
                customdata=sub[["fleet_fn", "total", "req", "av", "gap", "local_pct", "month"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Month: %{customdata[6]}<br>"
                    "Pilots: %{customdata[1]}<br>"
                    "Required: %{customdata[2]}<br>"
                    "Available: %{customdata[3]:.1f}<br>"
                    "Gap: %{customdata[4]:.1f}<br>"
                    "Local %: %{customdata[5]:.0f}%<extra></extra>"
                ),
            )],
        ))

    if not frames:
        info_panel("No data to display.")
        return

    # Initial trace = first frame's data
    fig.add_trace(frames[0].data[0])
    fig.frames = frames

    # Slider
    steps = []
    for i, fr in enumerate(frames):
        steps.append(dict(
            method="animate",
            label=fr.name,
            args=[[fr.name], dict(mode="immediate", frame=dict(duration=0, redraw=True),
                                  transition=dict(duration=250))],
        ))

    fig.update_layout(
        height=620,
        xaxis=dict(title="Localisation %", range=[-5, 105]),
        yaxis=dict(title="Available pilots"),
        margin=dict(l=60, r=30, t=40, b=90),
        sliders=[dict(
            active=0, currentvalue=dict(prefix="Month: ", font=dict(size=12)),
            pad=dict(t=30), steps=steps,
        )],
        updatemenus=[dict(
            type="buttons", showactive=False,
            x=0.02, y=-0.15, xanchor="left", yanchor="top",
            buttons=[
                dict(label="▶ Play", method="animate",
                     args=[None, dict(frame=dict(duration=450, redraw=True),
                                      fromcurrent=True,
                                      transition=dict(duration=200))]),
                dict(label="⏸ Pause", method="animate",
                     args=[[None], dict(frame=dict(duration=0, redraw=False),
                                        mode="immediate")]),
            ],
        )],
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Each bubble is a fleet × function. X-axis = localisation %. Y-axis = "
        "available pilots. Size = total pilots in that group. Colour = gap "
        "severity (green/amber/red). Hit ▶ Play to watch the plan unfold."
    )


def _flow_network(d, up_to_month: int):
    """
    Network graph: fleets as hub nodes, training transitions as edges.
    Edge thickness = number of pilots taking that route across the plan.
    Node size = pilots on that fleet at snapshot month.
    """
    ss = st.session_state

    # Nodes: one per fleet × function (8 nodes)
    nodes = []
    node_idx: dict[tuple[str, str], int] = {}
    for i, f in enumerate(FLEETS):
        for j, fn in enumerate(FUNCTIONS):
            node_idx[(f, fn)] = len(nodes)
            # Hub-and-spoke layout — fleets around a circle, CPT outer, FO inner
            import math
            angle = 2 * math.pi * i / len(FLEETS)
            radius = 1.0 if fn == "Captain" else 0.55
            nodes.append({
                "x": radius * math.cos(angle),
                "y": radius * math.sin(angle),
                "label": f"{f}<br>{fn[:3]}",
                "fleet": f,
                "function": fn,
                "size": max(10, d["avail"][f][fn][up_to_month] * 2.2),
                "req": d["req"][f][fn][up_to_month],
                "av": d["avail"][f][fn][up_to_month],
            })

    # Edges: aggregate planned actions into transitions
    edge_counts: dict[tuple[int, int], int] = {}
    edge_actions: dict[tuple[int, int], list[str]] = {}
    for a in ss.actions:
        if a.start_month + a.duration > up_to_month + 1:
            continue
        if a.action_type == "Type Rating":
            if (a.from_fleet, a.from_function) in node_idx and (a.to_fleet, a.to_function) in node_idx:
                key = (node_idx[(a.from_fleet, a.from_function)],
                       node_idx[(a.to_fleet, a.to_function)])
                edge_counts[key] = edge_counts.get(key, 0) + len(a.trainee_ids)
                edge_actions.setdefault(key, []).append(
                    f"TR {a.from_fleet} → {a.to_fleet} at {d['labels'][a.start_month]}"
                )
        elif a.action_type == "Command Upgrade":
            if (a.from_fleet, "First Officer") in node_idx and (a.to_fleet, "Captain") in node_idx:
                key = (node_idx[(a.from_fleet, "First Officer")],
                       node_idx[(a.to_fleet, "Captain")])
                edge_counts[key] = edge_counts.get(key, 0) + len(a.trainee_ids)
                edge_actions.setdefault(key, []).append(
                    f"CU {a.from_fleet} → {a.to_fleet} CPT at {d['labels'][a.start_month]}"
                )

    fig = go.Figure()

    # Edges first (so nodes sit on top)
    for (src, tgt), count in edge_counts.items():
        x0, y0 = nodes[src]["x"], nodes[src]["y"]
        x1, y1 = nodes[tgt]["x"], nodes[tgt]["y"]
        # Curved line via midpoint offset
        mx = (x0 + x1) / 2 + (y1 - y0) * 0.15
        my = (y0 + y1) / 2 - (x1 - x0) * 0.15
        fig.add_trace(go.Scatter(
            x=[x0, mx, x1], y=[y0, my, y1],
            mode="lines",
            line=dict(color=COLORS["accent"], width=min(12, 2 + count * 1.5)),
            opacity=0.5, hoverinfo="skip", showlegend=False,
        ))
        # Label on midpoint
        fig.add_trace(go.Scatter(
            x=[mx], y=[my],
            mode="markers+text",
            marker=dict(size=18, color="white",
                        line=dict(color=COLORS["accent"], width=2)),
            text=[f"<b>{count}</b>"],
            textfont=dict(size=10, color=COLORS["navy"]),
            hovertext="<br>".join(edge_actions[(src, tgt)]),
            hoverinfo="text", showlegend=False,
        ))

    # Nodes
    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    labels = [n["label"] for n in nodes]
    sizes = [n["size"] for n in nodes]
    colors = [_fleet_function_color(n["fleet"], n["function"]) for n in nodes]
    hovers = [
        f"<b>{n['fleet']} {n['function']}</b><br>"
        f"Required: {n['req']}<br>Available: {n['av']:.1f}<br>"
        f"Gap: {max(0, n['req'] - n['av']):.1f}"
        for n in nodes
    ]
    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="markers+text",
        marker=dict(size=sizes, color=colors,
                    line=dict(color="white", width=2)),
        text=labels, textposition="middle center",
        textfont=dict(size=10, color="white", family="Inter"),
        hovertext=hovers, hoverinfo="text", showlegend=False,
    ))

    fig.update_layout(
        height=640,
        xaxis=dict(visible=False, range=[-1.4, 1.4]),
        yaxis=dict(visible=False, range=[-1.3, 1.3],
                   scaleanchor="x", scaleratio=1),
        margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Fleets arranged around a circle — Captains on the outer ring, First "
        "Officers on the inner ring. Node size reflects pilot count at the "
        "snapshot month. Arrows are pilot transitions; the number in each "
        "bubble is the cohort size crossing that link by the snapshot."
    )


# ---------------------------------------------------------------------------
# Main — with per-tab crash isolation
# ---------------------------------------------------------------------------
def main():
    _autosave()
    render_topbar()

    tabs = st.tabs([
        "📊 Dashboard", "👥 Registry", "✈ Fleet Planner", "📅 Timeline",
        "🎯 Action Planner", "🌐 Flow Map", "🌏 Localisation",
        "🤖 AI Optimiser", "🖨 Print Plan",
    ])

    tab_funcs = [
        tab_dashboard, tab_registry, tab_fleet_planner, tab_timeline,
        tab_action_planner, tab_flow_map, tab_localisation,
        tab_ai_optimiser, tab_print_plan,
    ]

    for tab, fn in zip(tabs, tab_funcs):
        with tab:
            try:
                fn()
            except Exception as e:
                st.error(f"Error in {fn.__name__}: {type(e).__name__}: {e}")
                st.caption(
                    "Your plan data is safe. Click **Save JSON** in the top bar "
                    "to download a backup before refreshing the page."
                )


if __name__ == "__main__":
    main()
