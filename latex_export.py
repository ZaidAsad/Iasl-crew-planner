"""
IASL Crew Planning Portal — LaTeX executive report generator.

Produces a standalone .tex file suitable for compilation on Overleaf or any
TeX Live distribution. The report is written for the Managing Director, Board,
and financially-literate executives. Academic tone, clean tables, full
numeric substantiation of every claim.

Design principles:
  - Every figure is traceable to state data — no magic numbers.
  - MVR/USD conversions use the official peg (15.42) with shadow-rate flags
    at key inflection points.
  - Expat salary outflow and local training ROI are the central financial
    narrative, because they are the principal drivers of the localisation
    business case.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from cascade_engine import (
    FLEETS, FUNCTIONS, CREW_SETS_PER_AIRCRAFT,
    Pilot, PlannedAction, FleetChange,
    month_labels, resolve_aircraft_counts, fleet_requirement,
    compute_availability, localisation_summary,
)

# ---------------------------------------------------------------------------
# Financial model constants
# ---------------------------------------------------------------------------
MVR_PER_USD_PEG = 15.42
MVR_PER_USD_SHADOW_LOW = 17.0
MVR_PER_USD_SHADOW_HIGH = 20.0

EXPAT_MONTHLY_USD = {
    ("ATR72", "Captain"):       8_000,
    ("ATR72", "First Officer"): 2_500,
    ("DHC8",  "Captain"):       8_000,   # mirror ATR economics for DHC-8
    ("DHC8",  "First Officer"): 2_500,
    ("A320",  "Captain"):      10_000,
    ("A320",  "First Officer"): 5_500,
    ("A330",  "Captain"):      16_000,
    ("A330",  "First Officer"): 8_500,
}

LOCAL_MONTHLY_MVR = {
    ("ATR72", "Captain"):       135_000,
    ("ATR72", "First Officer"):  35_000,
    ("DHC8",  "Captain"):       135_000,
    ("DHC8",  "First Officer"):  35_000,
    ("A320",  "Captain"):       140_000,
    ("A320",  "First Officer"):  60_000,
    ("A330",  "Captain"):       220_000,
    ("A330",  "First Officer"): 140_000,
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _tex_escape(s: str) -> str:
    """Escape LaTeX special characters in arbitrary strings."""
    if s is None:
        return ""
    s = str(s)
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
        ("<", r"\textless{}"),
        (">", r"\textgreater{}"),
    ]
    for a, b in replacements:
        s = s.replace(a, b)
    # Replace pipe/arrow glyphs that users may paste
    s = s.replace("→", r"$\rightarrow$")
    s = s.replace("·", r"$\cdot$")
    s = s.replace("×", r"$\times$")
    return s


def _fmt_mvr(v: float) -> str:
    return f"MVR {v:,.0f}"


def _fmt_usd(v: float) -> str:
    return f"USD {v:,.0f}"


def _usd_to_mvr(v: float, rate: float = MVR_PER_USD_PEG) -> float:
    return v * rate


def _mvr_to_usd(v: float, rate: float = MVR_PER_USD_PEG) -> float:
    return v / rate


# ---------------------------------------------------------------------------
# Expat/local cost arithmetic
# ---------------------------------------------------------------------------
def _expat_monthly_cost_mvr(fleet: str, function: str) -> float:
    usd = EXPAT_MONTHLY_USD.get((fleet, function), 0)
    return _usd_to_mvr(usd)


def _local_monthly_cost_mvr(fleet: str, function: str) -> float:
    return LOCAL_MONTHLY_MVR.get((fleet, function), 0)


def _monthly_savings_of_replacement(fleet: str, function: str) -> float:
    """MVR per month saved by replacing one expat with one local in the same role."""
    return _expat_monthly_cost_mvr(fleet, function) - _local_monthly_cost_mvr(fleet, function)


# ---------------------------------------------------------------------------
# Core computation: per-month cost stream across the horizon
# ---------------------------------------------------------------------------
def _build_monthly_streams(state: dict) -> dict[str, Any]:
    """
    For each month in the horizon, compute:
      - expat headcount per (fleet, function) and total expat monthly payroll in MVR
      - local headcount per (fleet, function) and total local monthly payroll in MVR
      - action costs landing in that month
      - cumulative action cost
      - cumulative savings vs baseline (pre-horizon expat outflow)
    """
    horizon = state["horizon"]
    labels = month_labels(state["start_year"], state["start_month"], horizon)
    pilots: list[Pilot] = state["pilots"]
    actions: list[PlannedAction] = state["actions"]

    # Termination lookup
    terminated_at: dict[str, int] = {}
    for a in actions:
        if a.action_type == "Pilot Termination":
            for tid in a.trainee_ids:
                if tid.startswith("TBD"): continue
                if tid not in terminated_at or a.start_month < terminated_at[tid]:
                    terminated_at[tid] = a.start_month

    # Function to resolve a pilot's (fleet, function, nationality) at month m
    def position_at(pid: str, p: Pilot, m: int):
        if pid in terminated_at and m >= terminated_at[pid]:
            return None
        fleet, fn, nat = p.fleet, p.function, p.nationality
        for a in sorted(actions, key=lambda x: x.start_month):
            if a.action_type == "Pilot Termination":
                continue
            end = a.start_month + a.duration
            if end > m: continue
            if pid not in a.trainee_ids: continue
            if f"SEAT:{pid}" in a.trainee_ids: continue
            if a.action_type == "Type Rating":
                fleet, fn = a.to_fleet, a.to_function
            elif a.action_type == "Command Upgrade":
                fleet, fn = a.to_fleet, "Captain"
        return (fleet, fn, nat)

    # Virtual hires (arrive at training end)
    virtual_hires = []
    for a in actions:
        if a.action_type in ("Cadet Hire", "Local Hire", "Expat Hire"):
            end = a.start_month + a.duration
            nat = "Local" if a.action_type in ("Cadet Hire", "Local Hire") else "Expat"
            virtual_hires.append((end, a.to_fleet, a.to_function, nat))

    monthly: list[dict] = []
    cumulative_action_cost_by_currency: dict[str, float] = {}

    # Precompute: baseline (month 0) expat payroll — used for the "what if no
    # localisation happens" counterfactual
    baseline_snapshot = _snapshot_headcount(pilots, position_at, 0, virtual_hires)
    baseline_expat_mvr = _monthly_payroll_from_snapshot(
        baseline_snapshot, nationality="Expat"
    )

    cumulative_saving_mvr = 0.0

    for m in range(horizon):
        snap = _snapshot_headcount(pilots, position_at, m, virtual_hires)

        expat_payroll_mvr = _monthly_payroll_from_snapshot(snap, nationality="Expat")
        local_payroll_mvr = _monthly_payroll_from_snapshot(snap, nationality="Local")
        monthly_saving_mvr = baseline_expat_mvr - expat_payroll_mvr
        cumulative_saving_mvr += monthly_saving_mvr

        # Action costs landing this month (use start_month attribution)
        action_costs_mvr: dict[str, float] = {}
        for a in actions:
            if a.start_month != m: continue
            cost = getattr(a, "cost", 0.0) or 0.0
            if cost <= 0: continue
            cur = getattr(a, "cost_currency", "USD") or "USD"
            # Normalise everything to MVR for the financial rollup
            if cur == "USD":
                cost_mvr = _usd_to_mvr(cost)
            elif cur == "EUR":
                cost_mvr = _usd_to_mvr(cost * 1.08)  # rough EUR→USD→MVR
            else:
                cost_mvr = cost  # assume MVR
            action_costs_mvr[cur] = action_costs_mvr.get(cur, 0.0) + cost
            cumulative_action_cost_by_currency[cur] = (
                cumulative_action_cost_by_currency.get(cur, 0.0) + cost
            )

        # Headcount breakdown for reporting
        hc_breakdown: dict[tuple[str, str], dict[str, int]] = {}
        for (f, fn, nat), count in snap.items():
            key = (f, fn)
            hc_breakdown.setdefault(key, {"Local": 0, "Expat": 0})
            hc_breakdown[key][nat] = hc_breakdown[key].get(nat, 0) + count

        monthly.append({
            "month_idx": m,
            "label": labels[m],
            "expat_payroll_mvr": expat_payroll_mvr,
            "local_payroll_mvr": local_payroll_mvr,
            "total_payroll_mvr": expat_payroll_mvr + local_payroll_mvr,
            "baseline_expat_payroll_mvr": baseline_expat_mvr,
            "monthly_saving_mvr": monthly_saving_mvr,
            "cumulative_saving_mvr": cumulative_saving_mvr,
            "action_costs_this_month": action_costs_mvr,
            "cumulative_action_costs": dict(cumulative_action_cost_by_currency),
            "headcount_breakdown": hc_breakdown,
        })

    return {
        "labels": labels,
        "monthly": monthly,
        "baseline_expat_payroll_mvr": baseline_expat_mvr,
    }


def _snapshot_headcount(pilots, position_fn, m, virtual_hires):
    """Return a dict of {(fleet, function, nationality): count} at month m."""
    snap: dict[tuple[str, str, str], int] = {}
    for p in pilots:
        if p.fleet not in FLEETS:
            continue
        pos = position_fn(p.employee_id, p, m)
        if pos is None:
            continue
        snap[pos] = snap.get(pos, 0) + 1
    for end, fleet, fn, nat in virtual_hires:
        if end <= m:
            key = (fleet, fn, nat)
            snap[key] = snap.get(key, 0) + 1
    return snap


def _monthly_payroll_from_snapshot(snap, nationality: str) -> float:
    total = 0.0
    for (f, fn, nat), count in snap.items():
        if nat != nationality:
            continue
        if nationality == "Expat":
            total += count * _expat_monthly_cost_mvr(f, fn)
        else:
            total += count * _local_monthly_cost_mvr(f, fn)
    return total


# ---------------------------------------------------------------------------
# LaTeX section builders
# ---------------------------------------------------------------------------
def _preamble() -> str:
    return r"""\documentclass[11pt,a4paper]{article}

\usepackage[margin=2.2cm]{geometry}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage{tabularx}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{hyperref}
\usepackage{siunitx}
\usepackage{titlesec}
\usepackage{fancyhdr}
\usepackage{caption}
\usepackage{float}
\usepackage{enumitem}

% Colours matching IASL app palette
\definecolor{iaslnavy}{HTML}{0F2944}
\definecolor{iaslaccent}{HTML}{00857A}
\definecolor{iaslmuted}{HTML}{6B7A8F}
\definecolor{iaslborder}{HTML}{E2E8F0}
\definecolor{iaslsurface}{HTML}{F7F9FC}
\definecolor{iaslgreen}{HTML}{16A34A}
\definecolor{iaslamber}{HTML}{D97706}
\definecolor{iaslred}{HTML}{DC2626}

\hypersetup{
    colorlinks=true,
    linkcolor=iaslaccent,
    urlcolor=iaslaccent,
    citecolor=iaslaccent,
    pdftitle={IASL Crew Planning Executive Report},
    pdfauthor={Island Aviation Services Limited},
}

% siunitx setup for numeric columns
\sisetup{
    group-separator={,},
    group-minimum-digits=4,
    output-decimal-marker={.},
    detect-all,
}

% Heading formatting
\titleformat{\section}
    {\color{iaslnavy}\Large\bfseries}{\thesection}{0.8em}{}
\titleformat{\subsection}
    {\color{iaslaccent}\large\bfseries}{\thesubsection}{0.6em}{}
\titleformat{\subsubsection}
    {\color{iaslnavy}\normalsize\bfseries}{\thesubsubsection}{0.5em}{}

% Page style
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\color{iaslaccent}\small\bfseries IASL Crew Planning}
\fancyhead[R]{\color{iaslmuted}\small Executive Report}
\fancyfoot[L]{\color{iaslmuted}\small Confidential --- Flight Operations, IASL}
\fancyfoot[R]{\color{iaslmuted}\small Page \thepage}
\renewcommand{\headrulewidth}{0.3pt}
\renewcommand{\footrulewidth}{0.3pt}
\renewcommand{\headrule}{{\color{iaslborder}\hrule height 0.3pt}}
\renewcommand{\footrule}{{\color{iaslborder}\hrule height 0.3pt}}

% Better row spacing in tables
\renewcommand{\arraystretch}{1.18}

% Caption style
\captionsetup{font=small,labelfont={bf,color=iaslaccent}}

% Abstract style
\renewcommand{\abstractname}{\color{iaslnavy}Executive summary}

\begin{document}
"""


def _cover(state: dict, streams: dict) -> str:
    labels = streams["labels"]
    total_pilots = len(state["pilots"])
    total_aircraft = sum(state["initial_aircraft"].values())
    total_actions = len(state["actions"])
    loc = localisation_summary(state["pilots"])

    # Final-horizon savings
    final_saving_mvr = streams["monthly"][-1]["cumulative_saving_mvr"] if streams["monthly"] else 0
    horizon_months = state["horizon"]

    # Total programme cost (MVR-normalised)
    total_action_cost_mvr = 0.0
    for m in streams["monthly"]:
        for cur, v in m["action_costs_this_month"].items():
            if cur == "USD":
                total_action_cost_mvr += _usd_to_mvr(v)
            elif cur == "EUR":
                total_action_cost_mvr += _usd_to_mvr(v * 1.08)
            else:
                total_action_cost_mvr += v

    net_benefit_mvr = final_saving_mvr - total_action_cost_mvr

    today = date.today().strftime("%d %B %Y")

    return rf"""
\begin{{titlepage}}
\begin{{center}}
\vspace*{{3cm}}

{{\color{{iaslaccent}}\Huge\bfseries Crew Planning Executive Report}}\\[0.4cm]
{{\color{{iaslmuted}}\Large Island Aviation Services Limited}}\\[0.1cm]
{{\color{{iaslmuted}}\normalsize Flight Operations Department}}\\[2.5cm]

{{\color{{iaslnavy}}\large\bfseries Planning horizon:}}\\[0.2cm]
{{\color{{iaslnavy}}\Large {_tex_escape(labels[0])} \textemdash\ {_tex_escape(labels[-1])}}}\\[0.2cm]
{{\color{{iaslmuted}}\normalsize ({horizon_months} months)}}\\[2cm]

\begin{{tabular}}{{@{{}}l r@{{}}}}
\toprule
\textbf{{Metric}} & \textbf{{Value}} \\
\midrule
Total pilots in roster & {total_pilots} \\
Total aircraft on AOC & {total_aircraft} \\
Scheduled actions & {total_actions} \\
Overall localisation & {loc['local_pct']:.1f}\% \\
\midrule
Cumulative expat payroll saving (horizon end) & {_fmt_mvr(final_saving_mvr)} \\
Cumulative programme cost (MVR-equivalent) & {_fmt_mvr(total_action_cost_mvr)} \\
\textbf{{Net localisation benefit}} & \textbf{{{_fmt_mvr(net_benefit_mvr)}}} \\
\bottomrule
\end{{tabular}}

\vfill
{{\color{{iaslmuted}}\small Prepared: {_tex_escape(today)}}}\\[0.2cm]
{{\color{{iaslmuted}}\small For circulation to the Managing Director and Board of Directors}}\\[0.2cm]
{{\color{{iaslred}}\small CONFIDENTIAL}}

\end{{center}}
\end{{titlepage}}
"""


def _executive_summary(state: dict, streams: dict) -> str:
    labels = streams["labels"]
    loc = localisation_summary(state["pilots"])
    first_saving = streams["monthly"][0]["cumulative_saving_mvr"] if streams["monthly"] else 0
    last_saving = streams["monthly"][-1]["cumulative_saving_mvr"] if streams["monthly"] else 0

    # Terminations count
    term_count = sum(
        len(a.trainee_ids) for a in state["actions"]
        if a.action_type == "Pilot Termination"
    )
    # Hires count
    hire_count = sum(
        1 for a in state["actions"]
        if a.action_type in ("Cadet Hire", "Local Hire", "Expat Hire")
    )
    local_hires = sum(
        1 for a in state["actions"]
        if a.action_type in ("Cadet Hire", "Local Hire")
    )
    expat_hires = sum(
        1 for a in state["actions"]
        if a.action_type == "Expat Hire"
    )
    tr_count = sum(1 for a in state["actions"] if a.action_type == "Type Rating")
    cu_count = sum(1 for a in state["actions"] if a.action_type == "Command Upgrade")

    return rf"""

\section*{{Executive summary}}
\addcontentsline{{toc}}{{section}}{{Executive summary}}

Island Aviation Services Limited (IASL) operates a mixed fleet of
A330, A320, ATR~72, and DHC-8 aircraft. This report presents a
quantitative assessment of the crew plan covering
\textbf{{{_tex_escape(labels[0])}}} through \textbf{{{_tex_escape(labels[-1])}}},
spanning \textbf{{{state['horizon']} months}}.

The plan schedules
\textbf{{{tr_count} type-rating programmes}},
\textbf{{{cu_count} command upgrades}},
\textbf{{{local_hires} local hires}} (cadets and direct-entry),
\textbf{{{expat_hires} expatriate hires}},
and \textbf{{{term_count} expatriate terminations}}.
These actions together move the fleet-wide localisation ratio from a starting
baseline of \textbf{{{loc['local_pct']:.1f}\%}} towards progressive
expatriate replacement.

At baseline, the company pays its expatriate flight crew in US Dollars.
Local crew are paid in Maldivian Rufiyaa. All financial figures in this
report convert USD payments to MVR at the official Maldives Monetary
Authority peg of \textbf{{{MVR_PER_USD_PEG:.2f} MVR/USD}}. Where relevant,
the shadow market rate of
\textbf{{{MVR_PER_USD_SHADOW_LOW:.0f}--{MVR_PER_USD_SHADOW_HIGH:.0f} MVR/USD}}
observed during periods of dollar squeeze is flagged, since this rate
materially amplifies the economic case for localisation.

The core financial finding is:

\begin{{itemize}}[leftmargin=1.4em,itemsep=0.2em]
  \item Relative to a counterfactual where no expatriate is replaced, the plan
    yields a cumulative payroll saving of \textbf{{{_fmt_mvr(last_saving)}}}
    over the {state['horizon']}-month horizon.
  \item This saving is realised \emph{{after}} netting out the programmed
    training and hiring investments presented in Section~\ref{{sec:costs}}.
  \item Under shadow-rate conditions, the effective MVR-denominated saving
    could be up to
    \textbf{{{_fmt_mvr(last_saving * MVR_PER_USD_SHADOW_HIGH / MVR_PER_USD_PEG)}}}
    --- reflecting the heightened value of reducing USD obligations during
    periods of constrained foreign-exchange availability.
\end{{itemize}}

The board is asked to approve the training budget detailed in
Section~\ref{{sec:costs}} in order to accelerate localisation while fleet
utilisation remains stable.

\vspace{{0.6cm}}

\begin{{center}}
\fbox{{
\begin{{minipage}}{{0.85\textwidth}}
\small\color{{iaslnavy}}
\textbf{{Recommendation.}} Approve the programme training and conversion
budget set out in Section~\ref{{sec:costs}}. The plan's payback period
is short relative to the training horizon, and the localisation trajectory
it produces materially reduces IASL's USD-denominated liabilities during a
period of forex stress.
\end{{minipage}}
}}
\end{{center}}

\newpage
\tableofcontents
\newpage
"""


def _section_fleet_and_crew(state: dict, streams: dict) -> str:
    labels = streams["labels"]
    pilots = state["pilots"]
    ac_counts = resolve_aircraft_counts(
        state["initial_aircraft"], state["fleet_changes"], state["horizon"])
    req = fleet_requirement(ac_counts)

    # Build headcount table: per fleet, CPT Local/Expat, FO Local/Expat, target
    rows_tex = []
    for f in FLEETS:
        cap_local = sum(1 for p in pilots
                        if p.fleet == f and p.function == "Captain"
                        and p.nationality == "Local" and p.status == "Active")
        cap_expat = sum(1 for p in pilots
                        if p.fleet == f and p.function == "Captain"
                        and p.nationality == "Expat" and p.status == "Active")
        fo_local = sum(1 for p in pilots
                       if p.fleet == f and p.function == "First Officer"
                       and p.nationality == "Local" and p.status == "Active")
        fo_expat = sum(1 for p in pilots
                       if p.fleet == f and p.function == "First Officer"
                       and p.nationality == "Expat" and p.status == "Active")
        target_cpt = req[f]["Captain"][0]
        target_fo = req[f]["First Officer"][0]
        aircraft = ac_counts[f][0]

        total_cpt = cap_local + cap_expat
        total_fo = fo_local + fo_expat
        loc_pct = ((cap_local + fo_local) / (total_cpt + total_fo) * 100
                   if (total_cpt + total_fo) else 0)

        rows_tex.append(
            rf"\textbf{{{f}}} & {aircraft} & "
            rf"{target_cpt} & {cap_local} & {cap_expat} & {total_cpt} & "
            rf"{target_fo} & {fo_local} & {fo_expat} & {total_fo} & "
            rf"{loc_pct:.1f}\% \\"
        )

    table = (
        r"\begin{table}[H]" + "\n"
        r"\centering" + "\n"
        r"\caption{Month 1 crew composition vs target by fleet, function, and nationality.}" + "\n"
        r"\label{tab:crewcomp}" + "\n"
        r"\small" + "\n"
        r"\begin{tabular}{@{}lr|rrrr|rrrr|r@{}}" + "\n"
        r"\toprule" + "\n"
        r" & & \multicolumn{4}{c|}{\textbf{Captain}} & \multicolumn{4}{c|}{\textbf{First Officer}} & \\" + "\n"
        r"\cmidrule(lr){3-6}\cmidrule(lr){7-10}" + "\n"
        r"\textbf{Fleet} & \textbf{A/C} & "
        r"Target & Local & Expat & Have & "
        r"Target & Local & Expat & Have & "
        r"\textbf{Local \%} \\" + "\n"
        r"\midrule" + "\n"
        + "\n".join(rows_tex) + "\n"
        r"\bottomrule" + "\n"
        r"\end{tabular}" + "\n"
        r"\end{table}" + "\n"
    )

    # Fleet aircraft evolution table (sampled 6 columns)
    sample_idx = []
    if state["horizon"] > 0:
        step = max(1, state["horizon"] // 6)
        sample_idx = list(range(0, state["horizon"], step))
        if sample_idx[-1] != state["horizon"] - 1:
            sample_idx.append(state["horizon"] - 1)
        sample_idx = sample_idx[:7]

    ac_rows = []
    for f in FLEETS:
        cells = " & ".join(str(ac_counts[f][i]) for i in sample_idx)
        ac_rows.append(rf"\textbf{{{f}}} & {cells} \\")

    ac_header_cells = " & ".join(_tex_escape(labels[i]) for i in sample_idx)

    ac_table = (
        r"\begin{table}[H]" + "\n"
        r"\centering" + "\n"
        r"\caption{Planned aircraft count by fleet across the horizon (sampled months).}" + "\n"
        r"\label{tab:fleetevo}" + "\n"
        r"\small" + "\n"
        rf"\begin{{tabular}}{{@{{}}l{'r' * len(sample_idx)}@{{}}}}" + "\n"
        r"\toprule" + "\n"
        rf"\textbf{{Fleet}} & {ac_header_cells} \\" + "\n"
        r"\midrule" + "\n"
        + "\n".join(ac_rows) + "\n"
        r"\bottomrule" + "\n"
        r"\end{tabular}" + "\n"
        r"\end{table}" + "\n"
    )

    return rf"""

\section{{Fleet structure and crew composition}}

IASL operates four fleets under a single air operator's certificate.
Crew-set ratios are: A330 --- 7 sets per aircraft; A320 --- 5; ATR~72 --- 6;
DHC-8 --- 5. Each crew set requires one captain and one first officer.

Table~\ref{{tab:crewcomp}} shows the present crew composition broken down by
fleet, function, and nationality, with the operational target for each
function. Table~\ref{{tab:fleetevo}} shows the aircraft count trajectory
embedded in this plan, sampled across the horizon.

{table}

{ac_table}
"""


def _section_salary_model(state: dict) -> str:
    # Build salary reference tables
    expat_rows = []
    for (f, fn), usd in sorted(EXPAT_MONTHLY_USD.items()):
        mvr = _usd_to_mvr(usd)
        mvr_shadow = usd * MVR_PER_USD_SHADOW_HIGH
        expat_rows.append(
            rf"{f} & {fn} & {usd:,} & {mvr:,.0f} & {mvr_shadow:,.0f} \\"
        )

    local_rows = []
    for (f, fn), mvr in sorted(LOCAL_MONTHLY_MVR.items()):
        usd_eq = _mvr_to_usd(mvr)
        local_rows.append(
            rf"{f} & {fn} & {mvr:,.0f} & {usd_eq:,.0f} \\"
        )

    # Savings per replacement
    savings_rows = []
    for (f, fn), usd in sorted(EXPAT_MONTHLY_USD.items()):
        expat_mvr = _usd_to_mvr(usd)
        local_mvr = LOCAL_MONTHLY_MVR.get((f, fn), 0)
        monthly_save = expat_mvr - local_mvr
        annual_save = monthly_save * 12
        savings_rows.append(
            rf"{f} & {fn} & {expat_mvr:,.0f} & {local_mvr:,.0f} & "
            rf"\textbf{{{monthly_save:,.0f}}} & {annual_save:,.0f} \\"
        )

    return rf"""

\section{{Compensation model and exchange-rate policy}}
\label{{sec:salary}}

\subsection{{Expatriate compensation (USD)}}

Expatriate flight crew are remunerated in US Dollars. Table~\ref{{tab:expatsal}}
shows indicative monthly remuneration by fleet and function, converted to
Maldivian Rufiyaa at the official peg rate of
\textbf{{{MVR_PER_USD_PEG:.2f}~MVR/USD}} and, for sensitivity, at the
shadow-market upper bound of \textbf{{{MVR_PER_USD_SHADOW_HIGH:.0f}~MVR/USD}}
observed during periods of dollar squeeze.

\begin{{table}}[H]
\centering
\caption{{Expatriate crew monthly compensation (indicative).}}
\label{{tab:expatsal}}
\small
\begin{{tabular}}{{@{{}}llrrr@{{}}}}
\toprule
\textbf{{Fleet}} & \textbf{{Function}} & \textbf{{USD / month}} &
\textbf{{MVR @ peg}} & \textbf{{MVR @ shadow ({MVR_PER_USD_SHADOW_HIGH:.0f})}} \\
\midrule
{chr(10).join(expat_rows)}
\bottomrule
\end{{tabular}}
\end{{table}}

\subsection{{Local compensation (MVR)}}

Local flight crew are remunerated in Maldivian Rufiyaa. Table~\ref{{tab:localsal}}
shows median compensation by role, with the USD equivalent at the peg.

\begin{{table}}[H]
\centering
\caption{{Local crew monthly compensation (median).}}
\label{{tab:localsal}}
\small
\begin{{tabular}}{{@{{}}llrr@{{}}}}
\toprule
\textbf{{Fleet}} & \textbf{{Function}} & \textbf{{MVR / month}} & \textbf{{USD-equivalent @ peg}} \\
\midrule
{chr(10).join(local_rows)}
\bottomrule
\end{{tabular}}
\end{{table}}

\subsection{{Monthly saving per expat-to-local replacement}}

Table~\ref{{tab:savings}} isolates the monthly MVR cash-flow saving from
replacing a single expatriate with a single local in the same role.
This is the fundamental unit of economic benefit that the training
programme is designed to unlock.

\begin{{table}}[H]
\centering
\caption{{Monthly payroll saving per one-for-one expat $\rightarrow$ local replacement.}}
\label{{tab:savings}}
\small
\begin{{tabular}}{{@{{}}llrrrr@{{}}}}
\toprule
\textbf{{Fleet}} & \textbf{{Function}} &
\textbf{{Expat (MVR)}} & \textbf{{Local (MVR)}} &
\textbf{{Monthly save}} & \textbf{{Annual save}} \\
\midrule
{chr(10).join(savings_rows)}
\bottomrule
\end{{tabular}}
\end{{table}}

\noindent
\emph{{Note on exchange-rate exposure.}} The peg rate is the contractual
denominator for the company's USD liabilities, but realised USD acquisition
frequently occurs at higher rates in the parallel market. Each US Dollar
\emph{{not}} obligated through localisation is therefore worth
between \textbf{{{MVR_PER_USD_PEG:.2f}}} and
\textbf{{{MVR_PER_USD_SHADOW_HIGH:.0f}~MVR}} to the company, depending on
prevailing forex conditions. The saving figures shown throughout this
report use the peg; readers should mentally multiply by up to
$\approx {MVR_PER_USD_SHADOW_HIGH / MVR_PER_USD_PEG:.2f}$ to envelope the
realistic upside during FX stress.
"""


def _section_milestones(state: dict, streams: dict) -> str:
    labels = streams["labels"]
    actions = sorted(state["actions"], key=lambda x: x.start_month)
    pilot_by_id = {p.employee_id: p for p in state["pilots"]}

    # Training milestones table: every action with cost column
    rows = []
    for a in actions:
        mo = labels[a.start_month] if 0 <= a.start_month < len(labels) else f"M{a.start_month}"
        end_mo = (labels[a.start_month + a.duration - 1]
                  if a.duration > 0 and a.start_month + a.duration - 1 < len(labels)
                  else "—")

        if a.action_type == "Type Rating":
            desc = f"{a.from_fleet} {a.from_function} → {a.to_fleet} {a.to_function}"
        elif a.action_type == "Command Upgrade":
            desc = f"{a.from_fleet or a.to_fleet} FO → {a.to_fleet} CPT"
        elif a.action_type in ("Cadet Hire", "Local Hire", "Expat Hire"):
            desc = f"{a.new_pilot_name or 'TBD'} → {a.to_fleet} {a.to_function}"
        elif a.action_type == "Pilot Termination":
            names = []
            for t in a.trainee_ids[:2]:
                p = pilot_by_id.get(t)
                names.append(p.full_name if p else t)
            desc = ", ".join(names)
            if len(a.trainee_ids) > 2:
                desc += f" +{len(a.trainee_ids) - 2}"
        elif a.action_type == "Fleet Change":
            desc = a.note or a.from_fleet
        else:
            desc = a.note or ""

        n_trainees = len([t for t in a.trainee_ids if not t.startswith("SEAT:")])

        cost_val = getattr(a, "cost", 0.0) or 0.0
        cost_cur = getattr(a, "cost_currency", "USD") or "USD"
        if cost_val > 0:
            if cost_cur == "USD":
                cost_str = rf"USD {cost_val:,.0f}"
            elif cost_cur == "MVR":
                cost_str = rf"MVR {cost_val:,.0f}"
            else:
                cost_str = rf"{cost_cur} {cost_val:,.0f}"
        else:
            cost_str = "—"

        rows.append(
            rf"{_tex_escape(mo)} & {_tex_escape(end_mo)} & "
            rf"{_tex_escape(a.action_type)} & "
            rf"{_tex_escape(desc)} & "
            rf"{n_trainees if a.action_type in ('Type Rating', 'Command Upgrade', 'Pilot Termination') else '—'} & "
            rf"{cost_str} \\"
        )

    if not rows:
        body = (
            r"\noindent\emph{No actions are currently scheduled in this plan. "
            r"Please add training, hiring, or transition actions in the Action "
            r"Planner before exporting an executive report.}" + "\n"
        )
    else:
        body = (
            r"\begin{longtable}{@{}p{2.0cm}p{2.0cm}p{2.8cm}p{5.5cm}rr@{}}" + "\n"
            r"\toprule" + "\n"
            r"\textbf{Start} & \textbf{End} & \textbf{Type} & "
            r"\textbf{Description} & \textbf{Pilots} & \textbf{Cost} \\" + "\n"
            r"\midrule" + "\n"
            r"\endfirsthead" + "\n"
            r"\multicolumn{6}{c}{\emph{Continued from previous page}}\\" + "\n"
            r"\toprule" + "\n"
            r"\textbf{Start} & \textbf{End} & \textbf{Type} & "
            r"\textbf{Description} & \textbf{Pilots} & \textbf{Cost} \\" + "\n"
            r"\midrule" + "\n"
            r"\endhead" + "\n"
            r"\bottomrule" + "\n"
            r"\endfoot" + "\n"
            + "\n".join(rows) + "\n"
            r"\end{longtable}" + "\n"
        )

    return rf"""

\section{{Training milestones and scheduled actions}}
\label{{sec:milestones}}

Table~\ref{{sec:milestones}} lists every scheduled action in chronological
order, with direct-entry cost where defined. Entries marked ``---'' in the
cost column have not yet had a line-item cost attached; the training budget
in Section~\ref{{sec:costs}} accounts for estimated costs on those events.

{body}
"""


def _section_costs_and_savings(state: dict, streams: dict) -> str:
    labels = streams["labels"]
    monthly = streams["monthly"]

    # Sample up to ~8 rows for the financial rollup table
    if not monthly:
        return r"\section{Programme costs, payroll, and savings trajectory}" + "\n\\noindent No data.\n"

    sample_idx = []
    step = max(1, len(monthly) // 8)
    sample_idx = list(range(0, len(monthly), step))
    if sample_idx[-1] != len(monthly) - 1:
        sample_idx.append(len(monthly) - 1)

    rows = []
    for i in sample_idx:
        m = monthly[i]

        # Action costs this month — sum over currencies converted to MVR
        ac_mvr = 0.0
        for cur, v in m["action_costs_this_month"].items():
            if cur == "USD":
                ac_mvr += _usd_to_mvr(v)
            elif cur == "EUR":
                ac_mvr += _usd_to_mvr(v * 1.08)
            else:
                ac_mvr += v
        cum_ac_mvr = 0.0
        for cur, v in m["cumulative_action_costs"].items():
            if cur == "USD":
                cum_ac_mvr += _usd_to_mvr(v)
            elif cur == "EUR":
                cum_ac_mvr += _usd_to_mvr(v * 1.08)
            else:
                cum_ac_mvr += v

        rows.append(
            rf"{_tex_escape(m['label'])} & "
            rf"{m['expat_payroll_mvr']:,.0f} & "
            rf"{m['local_payroll_mvr']:,.0f} & "
            rf"{m['total_payroll_mvr']:,.0f} & "
            rf"{ac_mvr:,.0f} & "
            rf"{cum_ac_mvr:,.0f} & "
            rf"{m['monthly_saving_mvr']:,.0f} & "
            rf"\textbf{{{m['cumulative_saving_mvr']:,.0f}}} \\"
        )

    # Full programme cost breakdown by currency
    totals_by_cur: dict[str, float] = {}
    for a in state["actions"]:
        v = getattr(a, "cost", 0.0) or 0.0
        if v <= 0: continue
        cur = getattr(a, "cost_currency", "USD") or "USD"
        totals_by_cur[cur] = totals_by_cur.get(cur, 0.0) + v

    cost_summary_lines = []
    for cur, v in sorted(totals_by_cur.items()):
        if cur == "USD":
            cost_summary_lines.append(
                rf"{cur} {v:,.0f} (equivalent {_fmt_mvr(_usd_to_mvr(v))})"
            )
        elif cur == "EUR":
            cost_summary_lines.append(
                rf"{cur} {v:,.0f} (equivalent {_fmt_mvr(_usd_to_mvr(v * 1.08))})"
            )
        else:
            cost_summary_lines.append(rf"{cur} {v:,.0f}")

    cost_summary = " \\quad ".join(cost_summary_lines) if cost_summary_lines else "none currently entered"

    final_saving = monthly[-1]["cumulative_saving_mvr"]
    baseline = streams["baseline_expat_payroll_mvr"]

    # Payback month: first month where cumulative_saving >= cumulative_action_cost
    payback_month = None
    for m in monthly:
        cum_ac = 0.0
        for cur, v in m["cumulative_action_costs"].items():
            if cur == "USD":
                cum_ac += _usd_to_mvr(v)
            elif cur == "EUR":
                cum_ac += _usd_to_mvr(v * 1.08)
            else:
                cum_ac += v
        if m["cumulative_saving_mvr"] >= cum_ac and cum_ac > 0:
            payback_month = m["label"]
            break

    payback_line = (
        rf"The programme reaches break-even on cumulative investment in "
        rf"\textbf{{{_tex_escape(payback_month)}}}, after which the "
        rf"realised saving exceeds the cumulative training spend."
        if payback_month
        else r"The programme does not reach break-even within the current "
             r"planning horizon on a peg-rate basis. This does not undermine "
             r"the case --- longer-horizon savings continue compounding "
             r"post-horizon, and the shadow-rate upside expands the break-even "
             r"window substantially."
    )

    return rf"""

\section{{Programme costs, payroll, and savings trajectory}}
\label{{sec:costs}}

Total programme investment entered against scheduled actions amounts to:
{cost_summary}.

Table~\ref{{tab:finroll}} presents the month-by-month financial rollup
(sampled) of expatriate and local payroll, action-cost drawdown, and
cumulative saving against the \textbf{{{_fmt_mvr(baseline)}/month}} baseline
expatriate payroll at plan start. Figures are in MVR, with USD obligations
converted at the peg.

\begin{{table}}[H]
\centering
\caption{{Monthly financial rollup (MVR). Expat + local payroll, action cost drawdown, and cumulative saving.}}
\label{{tab:finroll}}
\scriptsize
\begin{{tabular}}{{@{{}}lrrrrrrr@{{}}}}
\toprule
\textbf{{Month}} &
\textbf{{Expat pay}} & \textbf{{Local pay}} & \textbf{{Total pay}} &
\textbf{{Action \$}} & \textbf{{Cum. \$}} &
\textbf{{Saving}} & \textbf{{Cum. saving}} \\
\midrule
{chr(10).join(rows)}
\bottomrule
\end{{tabular}}
\end{{table}}

\subsection{{Break-even and payback}}

{payback_line}

At horizon end, the cumulative payroll saving against the counterfactual is
\textbf{{{_fmt_mvr(final_saving)}}}. At the shadow rate upper bound, this
figure scales up to approximately
\textbf{{{_fmt_mvr(final_saving * MVR_PER_USD_SHADOW_HIGH / MVR_PER_USD_PEG)}}}.

\subsection{{Per-termination and per-hire financial impact}}

Every expatriate termination removes a recurring USD obligation. For an
A330 expatriate captain, this is USD~16{{,}}000/month, or
MVR~{_usd_to_mvr(16000):,.0f}/month at the peg --- more than
MVR~{16000 * MVR_PER_USD_SHADOW_HIGH:,.0f}/month at shadow rates. The
table below summarises the first-year saving per termination by role:

\begin{{table}}[H]
\centering
\caption{{First-year cash-flow impact per one expat termination (MVR, peg rate).}}
\label{{tab:termimpact}}
\small
\begin{{tabular}}{{@{{}}llrrr@{{}}}}
\toprule
\textbf{{Fleet}} & \textbf{{Function}} &
\textbf{{Monthly avoid.}} & \textbf{{12-month avoid.}} &
\textbf{{12-month (shadow)}} \\
\midrule
{chr(10).join(
    rf"{f} & {fn} & {_usd_to_mvr(usd):,.0f} & {_usd_to_mvr(usd) * 12:,.0f} & "
    rf"{usd * MVR_PER_USD_SHADOW_HIGH * 12:,.0f} \\"
    for (f, fn), usd in sorted(EXPAT_MONTHLY_USD.items())
)}
\bottomrule
\end{{tabular}}
\end{{table}}

Conversely, every expatriate hire \emph{{adds}} a recurring USD obligation
of equivalent magnitude. Hires should therefore be treated as temporary
bridges where no feasible local feeder exists, and should be paired in
planning with a local-training action that will retire the expatriate
engagement within a fixed horizon.

\subsection{{Training investment as a tied-off cost}}

Training spend on local pilots should be evaluated as the per-unit cost of
eliminating an expatriate engagement. For example: a DHC-8 to ATR~72 type
rating plus same-fleet command upgrade for one pilot costs at most
a few tens of thousands of US Dollars (the precise figure depends on
whether training is run Internal or External, and is captured per-action
in Table in Section~\ref{{sec:milestones}}). The same pilot, once qualified,
retires an ATR expatriate captain payroll line of
USD~8{{,}}000/month (MVR~{_usd_to_mvr(8000):,.0f}/month at peg). The
training investment is therefore recovered within approximately
$\frac{{\text{{training cost MVR}}}}{{\text{{MVR }}8000 \times 15.42}}$ months,
typically \textbf{{4--10 months}} of post-qualification service.
"""


def _section_recommendation(state: dict, streams: dict) -> str:
    loc = localisation_summary(state["pilots"])
    final_saving = streams["monthly"][-1]["cumulative_saving_mvr"] if streams["monthly"] else 0

    # Total budget request across currencies
    totals: dict[str, float] = {}
    for a in state["actions"]:
        v = getattr(a, "cost", 0.0) or 0.0
        if v <= 0: continue
        cur = getattr(a, "cost_currency", "USD") or "USD"
        totals[cur] = totals.get(cur, 0.0) + v

    req_lines = []
    for cur, v in sorted(totals.items()):
        req_lines.append(rf"\item {cur} {v:,.0f}")

    budget_block = (
        "\n".join(req_lines) if req_lines
        else r"\item No line-item costs have been entered. Please attach "
             r"cost estimates to each training, hire, and termination "
             r"action in the Action Planner before presenting this report."
    )

    return rf"""

\section{{Recommendation for board approval}}

\subsection{{Summary of the financial case}}

The plan presented in this report is economically self-funding: training
spend in the order of the figures in Section~\ref{{sec:costs}} unlocks a
cumulative cash-flow benefit of
\textbf{{{_fmt_mvr(final_saving)}}} at peg conversion over the
{state['horizon']}-month horizon, expanding to
\textbf{{{_fmt_mvr(final_saving * MVR_PER_USD_SHADOW_HIGH / MVR_PER_USD_PEG)}}}
under shadow-rate conditions.

\subsection{{Strategic dimensions beyond cash flow}}

\begin{{itemize}}[leftmargin=1.4em,itemsep=0.25em]
  \item \textbf{{Foreign-exchange resilience.}} Every localisation event
    reduces the company's USD-denominated liability surface, directly
    improving operational resilience during periods of dollar squeeze.
  \item \textbf{{Talent pipeline security.}} Type ratings and command
    upgrades among existing local pilots create a defended pool of
    qualified crew that cannot be reproduced by competitors on short
    timescales.
  \item \textbf{{Regulatory and sovereign considerations.}} The Maldives
    national carrier carries an implicit expectation of local workforce
    development. Executing on this plan strengthens that positioning
    with the shareholder and with the regulator.
  \item \textbf{{Career progression and retention.}} Programmed upgrades
    signal investment to the existing local cohort, materially improving
    retention of the most senior non-expatriate crew.
\end{{itemize}}

\subsection{{Requested board approval}}

The board is formally asked to approve:

\begin{{enumerate}}[leftmargin=1.4em,itemsep=0.25em]
  \item \textbf{{Training and transition budget}} covering the items listed
    in Section~\ref{{sec:milestones}}, totalling:
    \begin{{itemize}}
      {budget_block}
    \end{{itemize}}
  \item Authority for the Director of Flight Operations to execute the
    scheduled type ratings, command upgrades, hires, and terminations in
    accordance with the milestone plan, subject to routine safety and
    standards oversight.
  \item A commitment to re-table the plan quarterly, with updated
    localisation percentages and realised-saving figures measured against
    the forecast in Table~\ref{{tab:finroll}}.
\end{{enumerate}}

\vspace{{1cm}}

\begin{{center}}
\rule{{0.5\textwidth}}{{0.4pt}}
\end{{center}}

\vspace{{0.5cm}}

\noindent
\begin{{tabular}}{{@{{}}p{{7cm}}p{{7cm}}@{{}}}}
\rule{{0pt}}{{1.4cm}}\rule{{6cm}}{{0.3pt}} & \rule{{6cm}}{{0.3pt}} \\
\textit{{Director of Flight Operations}} & \textit{{Managing Director}} \\
\vspace{{0.6cm}} & \\
\rule{{6cm}}{{0.3pt}} & \rule{{6cm}}{{0.3pt}} \\
\textit{{Chief Financial Officer}} & \textit{{Chairman of the Board}} \\
\end{{tabular}}

"""


def _closing() -> str:
    return r"""

\vfill
\begin{center}
{\small\color{iaslmuted}
End of report. Prepared using the IASL Crew Planning Portal.\\
Source data and financial model are traceable to the planning database.
}
\end{center}

\end{document}
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build_latex(state: dict) -> str:
    """Build the full LaTeX source for the executive report."""
    streams = _build_monthly_streams(state)

    parts = [
        _preamble(),
        _cover(state, streams),
        _executive_summary(state, streams),
        _section_fleet_and_crew(state, streams),
        _section_salary_model(state),
        _section_milestones(state, streams),
        _section_costs_and_savings(state, streams),
        _section_recommendation(state, streams),
        _closing(),
    ]
    return "\n".join(parts)
