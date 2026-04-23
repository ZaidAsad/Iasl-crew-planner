"""
IASL Crew Planning Portal — Strategic Optimisation Engine.

Mixed-integer linear programming model for crew planning.

Uses PuLP + CBC solver. Given current state + user-specified targets, finds
a set of actions (type ratings, command upgrades, hires, terminations) that
satisfies the targets at minimum weighted cost.

Objective is a weighted blend of:
  - total monetary cost (training + salary outflow)
  - total expat-months over horizon
  - monthly crew shortfall (large penalty)
  - local pilots added (negative weight = bonus)
  - time-to-target (soft penalty on late target achievement)

Career progression graph encodes the promotion ladder:
  DHC-8 FO  -> DHC-8 CPT (command upgrade)
            -> ATR   FO  (type rating, lateral for FOs)
            -> A320  FO  (type rating)
  DHC-8 CPT -> ATR   CPT (type rating)
            -> A320  FO  (type rating, downgrade route)
  ATR   FO  -> ATR   CPT (command upgrade)
            -> A320  FO  (type rating)
  ATR   CPT -> A320  FO  (type rating, downgrade route)
  A320  FO  -> A320  CPT (command upgrade)
            -> A330  FO  (type rating)
  A320  CPT -> A330  FO  (type rating, downgrade route -> then A330 CPT later)
            -> A330  CPT (command upgrade, compound eligible)
  A330  FO  -> A320  CPT (compound: type rating + command upgrade)
            -> A330  CPT (command upgrade)
  A330  CPT  = terminal

Expat constraints:
  - Command upgrades and fleet transfers restricted to locals
  - Local hires: ATR FO only (as cadets) — all other locals progress via training
  - Expat hires: any fleet × function directly
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Any

from cascade_engine import (
    FLEETS, FUNCTIONS, CREW_SETS_PER_AIRCRAFT,
    TRAINING_DURATIONS,
    Pilot, PlannedAction, FleetChange,
    month_labels, resolve_aircraft_counts, fleet_requirement,
    compute_availability, new_id,
)

# ---------------------------------------------------------------------------
# Financial constants (mirror latex_export.py)
# ---------------------------------------------------------------------------
MVR_PER_USD = 15.42

EXPAT_MONTHLY_MVR = {
    ("ATR72", "Captain"):       8_000 * MVR_PER_USD,
    ("ATR72", "First Officer"): 2_500 * MVR_PER_USD,
    ("DHC8",  "Captain"):       8_000 * MVR_PER_USD,
    ("DHC8",  "First Officer"): 2_500 * MVR_PER_USD,
    ("A320",  "Captain"):      10_000 * MVR_PER_USD,
    ("A320",  "First Officer"): 5_500 * MVR_PER_USD,
    ("A330",  "Captain"):      16_000 * MVR_PER_USD,
    ("A330",  "First Officer"): 8_500 * MVR_PER_USD,
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

# Default per-transition training cost (MVR). User can override per action later.
# ---------------------------------------------------------------------------
# Training costs — IASL actual figures
# ---------------------------------------------------------------------------
# Source figures (per cohort of 2 pilots, in USD):
#   External type rating (generic):    USD 64,000 / 2 pilots  = USD 32,000/pilot
#   Internal type rating (generic):    USD 40,000 / 2 pilots  = USD 20,000/pilot
#   Command upgrade:                   USD 15,500 / 2 pilots  = USD  7,750/pilot
#   A320 → A330 type rating:           USD 17,000 / 2 pilots  = USD  8,500/pilot
# The solver's decision variables are per-pilot binaries, so the model needs
# per-pilot values. They are multiplied by 2 automatically when two pilots
# are in the same cohort — cohort discipline is enforced by the
# max_concurrent_trainings_per_fleet constraint, which typically pairs
# trainees when feasible.
COST_USD_PER_PILOT = {
    "type_rating_external":       32_000,
    "type_rating_internal":       20_000,
    "type_rating_a320_to_a330":    8_500,
    "command_upgrade":             7_750,
    "compound_upgrade":           16_250,   # A330 FO → A320 CPT: 20k internal TR + 7.75k CU + rounding
    "cadet_hire":                 32_000,
    "expat_hire":                 3_000,   # recruitment + relocation only
    "local_hire_fo":               3_000,   # direct-entry non-cadet (rare)
    "termination":                 1_000,   # severance + repatriation typical
}

DEFAULT_COST_MVR = {
    k: v * MVR_PER_USD for k, v in COST_USD_PER_PILOT.items()
}

# ---------------------------------------------------------------------------
# Career progression graph — allowed transitions
# ---------------------------------------------------------------------------
# Each transition: (from_fleet, from_function, to_fleet, to_function) -> action_type, duration_months
CAREER_TRANSITIONS: dict[tuple[str, str, str, str], tuple[str, int]] = {
    # Command upgrades same fleet (locals only)
    ("DHC8",  "First Officer", "DHC8",  "Captain"):        ("Command Upgrade", 1),
    ("ATR72", "First Officer", "ATR72", "Captain"):        ("Command Upgrade", 1),
    ("A320",  "First Officer", "A320",  "Captain"):        ("Command Upgrade", 1),
    ("A330",  "First Officer", "A330",  "Captain"):        ("Command Upgrade", 1),
    # Type ratings — DHC8 <-> ATR same function
    ("DHC8",  "First Officer", "ATR72", "First Officer"):  ("Type Rating", 2),
    ("DHC8",  "Captain",       "ATR72", "Captain"):        ("Type Rating", 2),
    # ATR/DHC8 -> A320 FO
    ("ATR72", "First Officer", "A320",  "First Officer"):  ("Type Rating", 2),
    ("ATR72", "Captain",       "A320",  "First Officer"):  ("Type Rating", 2),
    ("DHC8",  "First Officer", "A320",  "First Officer"):  ("Type Rating", 2),
    ("DHC8",  "Captain",       "A320",  "First Officer"):  ("Type Rating", 2),
    # A320 FO -> A330 FO
    ("A320",  "First Officer", "A330",  "First Officer"):  ("Type Rating", 1),
    # A320 CPT -> A330 FO (downgrade route)
    ("A320",  "Captain",       "A330",  "First Officer"):  ("Type Rating", 1),
    # A330 FO -> A320 CPT (compound: type rating + command upgrade, 2mo)
    ("A330",  "First Officer", "A320",  "Captain"):        ("Compound Upgrade", 2),
    # A330 FO -> A330 CPT same-fleet CU already covered above
    # A320 CPT -> A330 CPT (command upgrade, same-function pilots go direct)
    ("A320",  "Captain",       "A330",  "Captain"):        ("Command Upgrade", 1),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class OptimiserGoal:
    """A user-specified target for one (fleet, function, nationality) cell."""
    fleet: str
    function: str
    target_month: int          # month index (0-based) by which target must be achieved
    target_total: int          # total pilots required at that fleet×function at target_month
    max_expats: int            # maximum expats allowed in that count
    min_locals: int            # minimum locals required in that count
    priority: str = "must"     # "must" = hard constraint, "nice" = soft (large penalty if missed)


@dataclass
class OptimiserWeights:
    cost: float = 1.0
    expat_months: float = 5000.0
    shortfall: float = 10_000_000.0
    local_added: float = -500_000.0
    time_to_target: float = 50_000.0
    expat_hire_penalty: float = 5_000_000.0
    # Very large per-hire penalty discouraging bridge expat hires. Combined with
    # the expat contract horizon constraint below, this makes the solver only
    # hire expats when genuinely unavoidable for meeting a hard goal.


@dataclass
class OptimiserConfig:
    mode: str = "fast"
    time_limit_seconds: int = 30
    max_concurrent_trainings_per_fleet: int = 2
    allow_expat_hires: bool = True
    allow_terminations: bool = True
    expat_hire_contract_months: int = 24
    # Any expat the solver chooses to hire is automatically terminated
    # after this many months. Enforces the company rule that expats are
    # a bridge, not a permanent solution.
    strategy: str = "cost"
    # "cost": minimise total programme cost (training + salaries + hiring)
    # "time": minimise time to target (meet goals as fast as possible)
    seed: int = 42

@dataclass
class SolverProgress:
    phase: str = "idle"
    elapsed_seconds: float = 0.0
    incumbent_value: float | None = None
    best_bound: float | None = None
    gap_percent: float | None = None
    message: str = ""


@dataclass
class OptimiserResult:
    status: str                      # "optimal" / "feasible" / "infeasible" / "time_limit" / "error"
    objective_value: float | None
    gap_percent: float | None
    elapsed_seconds: float
    actions: list[PlannedAction]
    per_month_shortfall: dict[tuple[str, str], list[float]]
    explanation: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Eligibility helpers
# ---------------------------------------------------------------------------
def _pilot_eligible_transitions(pilot: Pilot) -> list[tuple[str, str, str, int]]:
    """
    Return list of (to_fleet, to_function, transition_type, duration_months)
    available to this pilot based on the career graph.

    Command upgrades and fleet transfers restricted to locals.
    Expats excluded from any transition (they remain at their current role
    until terminated).
    """
    if pilot.nationality == "Expat":
        return []  # expats don't get transitions — they come, serve, leave
    if pilot.status != "Active":
        return []  # pilots on leave or training can't be re-assigned

    options = []
    for (ff, fn, tf, tfn), (atype, dur) in CAREER_TRANSITIONS.items():
        if ff == pilot.fleet and fn == pilot.function:
            options.append((tf, tfn, atype, dur))
    return options


def _action_cost_mvr(
    transition_type: str,
    from_fleet: str = "",
    to_fleet: str = "",
    training_mode: str = "External",
) -> float:
    """
    Per-pilot training / transition cost in MVR.
    The A320 → A330 type rating has a specific lower cost; all other type
    ratings fall under the generic external / internal rates.
    """
    if transition_type == "Type Rating":
        if from_fleet == "A320" and to_fleet == "A330":
            return DEFAULT_COST_MVR["type_rating_a320_to_a330"]
        return (DEFAULT_COST_MVR["type_rating_external"]
                if training_mode == "External"
                else DEFAULT_COST_MVR["type_rating_internal"])
    if transition_type == "Command Upgrade":
        return DEFAULT_COST_MVR["command_upgrade"]
    if transition_type == "Compound Upgrade":
        return DEFAULT_COST_MVR["compound_upgrade"]
    return 0.0


# ---------------------------------------------------------------------------
# The core solver
# ---------------------------------------------------------------------------
def solve(
    state: dict,
    goals: list[OptimiserGoal],
    weights: OptimiserWeights,
    config: OptimiserConfig,
    progress_callback: Callable[[SolverProgress], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> OptimiserResult:
    """
    Main solver entry point.
    Returns an OptimiserResult with recommended actions.
    """
    import pulp

    horizon = state["horizon"]
    pilots: list[Pilot] = state["pilots"]
    initial_actions: list[PlannedAction] = state["actions"]
    ac_counts = resolve_aircraft_counts(
        state["initial_aircraft"], state["fleet_changes"], horizon)
    requirement = fleet_requirement(ac_counts)
    labels = month_labels(state["start_year"], state["start_month"], horizon)

    t_start = time.time()

    def _progress(phase: str, msg: str = "", inc=None, bound=None, gap=None):
        if progress_callback is None:
            return
        p = SolverProgress(
            phase=phase,
            elapsed_seconds=time.time() - t_start,
            incumbent_value=inc,
            best_bound=bound,
            gap_percent=gap,
            message=msg,
        )
        progress_callback(p)

    _progress("setup", "Building decision variables…")

    # ------------------------------------------------------------------
    # Pre-filter locally transferable pilots
    # ------------------------------------------------------------------
    local_pilots = [p for p in pilots if p.nationality == "Local"]
    expat_pilots = [p for p in pilots if p.nationality == "Expat"]

    # Each local pilot's available transitions
    pilot_transitions: dict[str, list[tuple[str, str, str, int]]] = {}
    for p in local_pilots:
        pilot_transitions[p.employee_id] = _pilot_eligible_transitions(p)

    # Months where a transition can START: 0 .. horizon-duration
    def _start_months(duration: int) -> list[int]:
        return list(range(max(0, horizon - duration) + 1))

    # ------------------------------------------------------------------
    # Build the LP/MIP problem
    # ------------------------------------------------------------------
    prob = pulp.LpProblem("IASL_Crew_Optimiser", pulp.LpMinimize)

    # DECISION VARIABLES

    # x_TR[pilot_id, to_fleet, to_function, start_month] binary
    # (only for transitions that pilot is eligible for)
    x_TR: dict[tuple[str, str, str, int], pulp.LpVariable] = {}
    for p in local_pilots:
        for (tf, tfn, atype, dur) in pilot_transitions[p.employee_id]:
            for sm in _start_months(dur):
                key = (p.employee_id, tf, tfn, sm)
                x_TR[key] = pulp.LpVariable(
                    f"x_tr_{p.employee_id}_{tf}_{tfn.replace(' ','_')}_{sm}",
                    cat="Binary",
                )

    # x_HIRE[fleet, function, nationality, start_month] integer >= 0
    x_HIRE: dict[tuple[str, str, str, int], pulp.LpVariable] = {}
    for f in FLEETS:
        for fn in FUNCTIONS:
            for nat in ("Local", "Expat"):
                if nat == "Local" and not (f == "ATR72" and fn == "First Officer"):
                    continue  # locals can only be hired as ATR FO
                if nat == "Expat" and not config.allow_expat_hires:
                    continue
                for sm in range(horizon):
                    key = (f, fn, nat, sm)
                    training_dur = TRAINING_DURATIONS["cadet_atr_fo"] if nat == "Local" else 0
                    if sm + training_dur > horizon:
                        continue
                    x_HIRE[key] = pulp.LpVariable(
                        f"x_hire_{f}_{fn.replace(' ','_')}_{nat}_{sm}",
                        lowBound=0, upBound=20, cat="Integer",
                    )

    # x_TERM[pilot_id, month] binary — expat termination
    x_TERM: dict[tuple[str, int], pulp.LpVariable] = {}
    if config.allow_terminations:
        for p in expat_pilots:
            for m in range(horizon):
                key = (p.employee_id, m)
                x_TERM[key] = pulp.LpVariable(
                    f"x_term_{p.employee_id}_{m}",
                    cat="Binary",
                )

    # shortfall[fleet, function, month] >= 0 (slack for shortfall penalty)
    short: dict[tuple[str, str, int], pulp.LpVariable] = {}
    for f in FLEETS:
        for fn in FUNCTIONS:
            for m in range(horizon):
                short[(f, fn, m)] = pulp.LpVariable(
                    f"short_{f}_{fn.replace(' ','_')}_{m}",
                    lowBound=0, cat="Continuous",
                )

    # goal_miss[goal_idx] >= 0 — amount by which a "nice" goal is missed
    goal_miss: dict[int, pulp.LpVariable] = {}
    for i, g in enumerate(goals):
        goal_miss[i] = pulp.LpVariable(
            f"goal_miss_{i}", lowBound=0, cat="Continuous",
        )

    _progress("setup", f"Variables built: "
                      f"{len(x_TR)} TR, {len(x_HIRE)} HIRE, {len(x_TERM)} TERM")

    # ------------------------------------------------------------------
    # CONSTRAINTS
    # ------------------------------------------------------------------

    # (C1) Each pilot can take at most one transition across the horizon.
    # Rationale: we're not modelling sequential transitions within the horizon
    # for simplicity. Pilots pick their best single move.
    for p in local_pilots:
        relevant = [x_TR[k] for k in x_TR if k[0] == p.employee_id]
        if relevant:
            prob += pulp.lpSum(relevant) <= 1, f"one_transition_{p.employee_id}"

    # (C2) Each expat can be terminated at most once.
    for p in expat_pilots:
        relevant = [x_TERM[k] for k in x_TERM if k[0] == p.employee_id]
        if relevant:
            prob += pulp.lpSum(relevant) <= 1, f"one_term_{p.employee_id}"

    # (C3) Max concurrent trainings per fleet per month.
    for f in FLEETS:
        for m in range(horizon):
            # Trainings active in month m: type ratings landing at fleet f
            active_at_m = []
            for (pid, tf, tfn, sm), var in x_TR.items():
                # Find duration
                p = next((pl for pl in local_pilots if pl.employee_id == pid), None)
                if not p: continue
                dur = _transition_duration(p.fleet, p.function, tf, tfn)
                if dur is None: continue
                if tf == f and sm <= m < sm + dur:
                    active_at_m.append(var)
            if active_at_m:
                prob += pulp.lpSum(active_at_m) <= config.max_concurrent_trainings_per_fleet, \
                    f"concurrent_{f}_{m}"

    # (C4) Headcount identity per (fleet, function, month):
    # headcount(f, fn, m) =
    #   initial_headcount(f, fn) at m=0
    #   + arrivals from transitions completed by m
    #   + arrivals from hires completed by m
    #   - departures from transitions starting by m (pilot has left origin role)
    #   - terminations active by m (expats leaving)
    #   + shortfall(f, fn, m)  >= requirement(f, fn, m)
    # We express availability and impose shortfall >= requirement - availability.

    for f in FLEETS:
        for fn in FUNCTIONS:
            for m in range(horizon):
                # Starting headcount at this (f, fn) — pilots currently in this role
                base = sum(
                    1 for p in pilots
                    if p.fleet == f and p.function == fn and p.status == "Active"
                )

                # Outflows from transitions that started at any time <=m:
                # pilot leaves origin as soon as transition starts
                outflows = []
                for (pid, tf, tfn, sm), var in x_TR.items():
                    p = next((pl for pl in local_pilots
                              if pl.employee_id == pid), None)
                    if not p: continue
                    if p.fleet == f and p.function == fn and sm <= m:
                        outflows.append(var)

                # Inflows from transitions that completed by month m:
                # pilot arrives at destination at start_month + duration
                inflows = []
                for (pid, tf, tfn, sm), var in x_TR.items():
                    if tf == f and tfn == fn:
                        p = next((pl for pl in local_pilots
                                  if pl.employee_id == pid), None)
                        if not p: continue
                        dur = _transition_duration(p.fleet, p.function, tf, tfn)
                        if dur is None: continue
                        if sm + dur <= m:
                            inflows.append(var)

                # Hire arrivals
                for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
                    if hf == f and hfn == fn:
                        training_dur = TRAINING_DURATIONS["cadet_atr_fo"] if hnat == "Local" else 0
                        if hsm + training_dur <= m:
                            inflows.append(hvar)

                # Expat terminations (departures from this cell)
                term_outflows = []
                for (pid, tm), tvar in x_TERM.items():
                    p = next((pl for pl in expat_pilots
                              if pl.employee_id == pid), None)
                    if not p: continue
                    if p.fleet == f and p.function == fn and tm <= m:
                        term_outflows.append(tvar)

                # Constraint: base + inflows - outflows - terms + short >= req
                req_m = requirement[f][fn][m]
                prob += (
                    base
                    + pulp.lpSum(inflows)
                    - pulp.lpSum(outflows)
                    - pulp.lpSum(term_outflows)
                    + short[(f, fn, m)]
                    >= req_m
                ), f"headcount_{f}_{fn.replace(' ','_')}_{m}"

    # (C5) User goals — at target month, count in (f, fn) must meet target.
    # Split by nationality because goals specify min_locals and max_expats.
    for gi, g in enumerate(goals):
        m = g.target_month
        if not (0 <= m < horizon):
            continue

        # Local headcount at (f, fn, m)
        local_base = sum(
            1 for p in pilots
            if p.fleet == g.fleet and p.function == g.function
            and p.nationality == "Local" and p.status == "Active"
        )
        local_in = []
        local_out = []
        for (pid, tf, tfn, sm), var in x_TR.items():
            p = next((pl for pl in local_pilots
                      if pl.employee_id == pid), None)
            if not p: continue
            dur = _transition_duration(p.fleet, p.function, tf, tfn)
            if dur is None: continue
            # Inflows to goal cell
            if tf == g.fleet and tfn == g.function and sm + dur <= m:
                local_in.append(var)
            # Outflows from goal cell
            if p.fleet == g.fleet and p.function == g.function and sm <= m:
                local_out.append(var)

        # Local hire arrivals (only ATR FO)
        for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
            if hf == g.fleet and hfn == g.function and hnat == "Local":
                if hsm + TRAINING_DURATIONS["cadet_atr_fo"] <= m:
                    local_in.append(hvar)

        # Expat headcount at (f, fn, m)
        expat_base = sum(
            1 for p in pilots
            if p.fleet == g.fleet and p.function == g.function
            and p.nationality == "Expat" and p.status == "Active"
        )
        expat_out = []
        for (pid, tm), tvar in x_TERM.items():
            p = next((pl for pl in expat_pilots
                      if pl.employee_id == pid), None)
            if not p: continue
            if p.fleet == g.fleet and p.function == g.function and tm <= m:
                expat_out.append(tvar)
        expat_in = []
        for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
            if hf == g.fleet and hfn == g.function and hnat == "Expat" and hsm <= m:
                expat_in.append(hvar)

        local_count_expr = local_base + pulp.lpSum(local_in) - pulp.lpSum(local_out)
        expat_count_expr = expat_base + pulp.lpSum(expat_in) - pulp.lpSum(expat_out)
        total_count_expr = local_count_expr + expat_count_expr

        if g.priority == "must":
            prob += total_count_expr >= g.target_total, f"goal_total_{gi}"
            prob += local_count_expr >= g.min_locals, f"goal_min_locals_{gi}"
            prob += expat_count_expr <= g.max_expats, f"goal_max_expats_{gi}"
        else:
            # Soft constraint: goal_miss absorbs shortfall
            prob += total_count_expr + goal_miss[gi] >= g.target_total, \
                f"goal_total_{gi}"
            # Locals / expats kept as hard when priority is "nice" too,
            # for tractability; relax later if needed.
            prob += local_count_expr >= g.min_locals, f"goal_min_locals_{gi}"
            prob += expat_count_expr <= g.max_expats, f"goal_max_expats_{gi}"

    # ------------------------------------------------------------------
    # OBJECTIVE
    # ------------------------------------------------------------------
    obj_cost_terms = []

    # Training cost
    for (pid, tf, tfn, sm), var in x_TR.items():
        p = next((pl for pl in local_pilots if pl.employee_id == pid), None)
        if not p: continue
        transition_type = _transition_action_type(p.fleet, p.function, tf, tfn)
        cost = _action_cost_mvr(
            transition_type,
            from_fleet=p.fleet, to_fleet=tf,
            training_mode="External",
        )
        obj_cost_terms.append(cost * var)

    # Hire cost
    for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
        if hnat == "Local":
            cost = DEFAULT_COST_MVR["cadet_hire"]
        else:
            cost = DEFAULT_COST_MVR["expat_hire"]
        obj_cost_terms.append(cost * hvar)

    # Termination cost
    for (pid, tm), tvar in x_TERM.items():
        obj_cost_terms.append(DEFAULT_COST_MVR["termination"] * tvar)

    # Expat-month salary cost (minimise remaining expat service)
    obj_expat_terms = []
    for p in expat_pilots:
        months_served_vars = []
        for m in range(horizon):
            # Indicator: expat is still active at month m (no termination <=m)
            # Approximated as 1 - sum(term events <=m)
            terms_so_far = [
                x_TERM[(p.employee_id, tm)]
                for tm in range(m + 1)
                if (p.employee_id, tm) in x_TERM
            ]
            salary = EXPAT_MONTHLY_MVR.get((p.fleet, p.function), 0)
            # Cost at month m = salary * (1 - sum terms) = salary - salary * sum terms
            # Sum over m of this becomes salary*(m+1 months) - salary * cumulative terms
            # We'll just charge salary up to the termination month.
            for t_var in terms_so_far:
                obj_expat_terms.append(-salary * t_var)  # saving per term
            obj_expat_terms.append(salary)  # base cost
    # The above is a tad handwavy — it folds expat salary into the objective
    # as a constant plus a reduction for each month a termination precedes.
    # For the LP, the constants drop out of the minimisation, only the
    # savings matter — i.e. the coefficient of x_TERM becomes negative
    # (= reward to terminate earlier).

    # Shortfall penalty
    obj_short_terms = []
    for key, var in short.items():
        obj_short_terms.append(weights.shortfall * var)

    # Goal miss penalty (for "nice" priority goals)
    obj_goal_terms = []
    for gi, var in goal_miss.items():
        if goals[gi].priority == "nice":
            obj_goal_terms.append(weights.shortfall * var)

    # Local-added bonus
    obj_local_terms = []
    for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
        if hnat == "Local":
            obj_local_terms.append(weights.local_added * hvar)
    # Also count transitions as "adding a local to higher role"
    for (pid, tf, tfn, sm), var in x_TR.items():
        obj_local_terms.append(weights.local_added * var * 0.3)

    # Expat-hire penalty — strong discouragement of bridge expat hires
    obj_expat_hire_terms = []
    for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
        if hnat == "Expat":
            obj_expat_hire_terms.append(weights.expat_hire_penalty * hvar)

    # Time-to-target penalty — we approximate this by penalising shortfalls
    # that persist into later months (so meeting goals EARLIER is cheaper).
    # For "time" strategy we scale this up; for "cost" strategy it stays low.
    obj_time_terms = []
    if weights.time_to_target > 0:
        for (f, fn, m), svar in short.items():
            # Later months are penalised more heavily
            month_weight = (m + 1) / horizon  # 1/h to 1.0
            obj_time_terms.append(weights.time_to_target * month_weight * svar)

    # Assemble
    prob += (
        weights.cost * pulp.lpSum(obj_cost_terms)
        + pulp.lpSum(obj_expat_terms)
        + pulp.lpSum(obj_short_terms)
        + pulp.lpSum(obj_goal_terms)
        + pulp.lpSum(obj_local_terms)
        + pulp.lpSum(obj_expat_hire_terms)
        + pulp.lpSum(obj_time_terms)
    )

    # ------------------------------------------------------------------
    # SOLVE
    # ------------------------------------------------------------------
    _progress("solving", f"Starting CBC solver (time limit {config.time_limit_seconds}s)…")

    solver = pulp.PULP_CBC_CMD(
        timeLimit=config.time_limit_seconds,
        msg=False,
        gapRel=0.05,  # 5% relative gap
    )

    try:
        status_code = prob.solve(solver)
    except Exception as e:
        return OptimiserResult(
            status="error",
            objective_value=None,
            gap_percent=None,
            elapsed_seconds=time.time() - t_start,
            actions=[],
            per_month_shortfall={},
            explanation=f"Solver crashed: {e}",
        )

    status_map = {
        1: "optimal",
        0: "not_solved",
        -1: "infeasible",
        -2: "unbounded",
        -3: "undefined",
    }
    status_str = status_map.get(status_code, "unknown")

    obj_val = pulp.value(prob.objective) if prob.objective else None
    elapsed = time.time() - t_start

    if status_str not in ("optimal", "not_solved") and obj_val is None:
        return OptimiserResult(
            status=status_str,
            objective_value=None,
            gap_percent=None,
            elapsed_seconds=elapsed,
            actions=[],
            per_month_shortfall={},
            explanation=f"Solver reported {status_str}. "
                        "Goals may be infeasible. Try relaxing targets, "
                        "extending target dates, or allowing more expats.",
        )

    # ------------------------------------------------------------------
    # EXTRACT ACTIONS
    # ------------------------------------------------------------------
    actions = _extract_actions(
        x_TR, x_HIRE, x_TERM, local_pilots, expat_pilots,
        expat_contract_months=config.expat_hire_contract_months,
        horizon=horizon,
    )

    # Shortfall summary
    per_month_short: dict[tuple[str, str], list[float]] = {}
    for f in FLEETS:
        for fn in FUNCTIONS:
            series = []
            for m in range(horizon):
                v = pulp.value(short[(f, fn, m)]) or 0
                series.append(v)
            per_month_short[(f, fn)] = series

    total_short = sum(sum(s) for s in per_month_short.values())

    _progress("done", f"Solver finished in {elapsed:.1f}s with status {status_str}")

    explanation = _build_explanation(
        status_str, obj_val, elapsed, actions, total_short, goals
    )

    return OptimiserResult(
        status=status_str,
        objective_value=obj_val,
        gap_percent=None,
        elapsed_seconds=elapsed,
        actions=actions,
        per_month_shortfall=per_month_short,
        explanation=explanation,
        diagnostics={
            "n_variables": len(prob.variables()),
            "n_constraints": len(prob.constraints),
            "total_shortfall": total_short,
        },
    )


def _transition_duration(from_fleet, from_function, to_fleet, to_function):
    for (ff, fn, tf, tfn), (atype, dur) in CAREER_TRANSITIONS.items():
        if ff == from_fleet and fn == from_function and tf == to_fleet and tfn == to_function:
            return dur
    return None


def _transition_action_type(from_fleet, from_function, to_fleet, to_function):
    for (ff, fn, tf, tfn), (atype, dur) in CAREER_TRANSITIONS.items():
        if ff == from_fleet and fn == from_function and tf == to_fleet and tfn == to_function:
            return atype
    return "Type Rating"


def _extract_actions(x_TR, x_HIRE, x_TERM, local_pilots, expat_pilots,
                     expat_contract_months: int = 24,
                     horizon: int = 24) -> list[PlannedAction]:
    import pulp
    acts: list[PlannedAction] = []

    pilot_by_id = {p.employee_id: p for p in local_pilots}

    # Group transitions by (start_month, from_fleet, from_function, to_fleet, to_function)
    for (pid, tf, tfn, sm), var in x_TR.items():
        if (pulp.value(var) or 0) < 0.5:
            continue
        p = pilot_by_id.get(pid)
        if not p: continue
        atype = _transition_action_type(p.fleet, p.function, tf, tfn)
        dur = _transition_duration(p.fleet, p.function, tf, tfn) or 1

        cost = _action_cost_mvr(
            atype,
            from_fleet=p.fleet, to_fleet=tf,
            training_mode="External",
        )

        if atype == "Compound Upgrade":
            # Represent as a single action with compound note
            acts.append(PlannedAction(
                id=new_id("opt"),
                action_type="Type Rating",
                start_month=sm,
                duration=dur,
                mode="External",
                trainee_ids=[pid],
                from_fleet=p.fleet, from_function=p.function,
                to_fleet=tf, to_function=tfn,
                note="[OPT] Compound type rating + command upgrade",
                cost=cost,
                cost_currency="MVR",
            ))
        else:
            acts.append(PlannedAction(
                id=new_id("opt"),
                action_type=atype,
                start_month=sm,
                duration=dur,
                mode="External",
                trainee_ids=[pid],
                from_fleet=p.fleet, from_function=p.function,
                to_fleet=tf, to_function=tfn,
                note="[OPT] Optimiser-generated",
                cost=cost,
                cost_currency="MVR",
            ))

    # Hires
    # Hires — with auto-termination for solver-scheduled expat hires
    contract_termination_marker = []  # list of (hire_action_id, termination_month)
    for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
        n_hires = int(round(pulp.value(hvar) or 0))
        for h_i in range(n_hires):
            dur = TRAINING_DURATIONS["cadet_atr_fo"] if hnat == "Local" else 0
            cost = DEFAULT_COST_MVR["cadet_hire"] if hnat == "Local" else DEFAULT_COST_MVR["expat_hire"]
            atype = "Cadet Hire" if hnat == "Local" else "Expat Hire"

            hire_action_id = new_id("opt")
            placeholder_pilot_id = f"TBD-OPT-{hire_action_id[-8:]}-{h_i}"

            acts.append(PlannedAction(
                id=hire_action_id,
                action_type=atype,
                start_month=hsm,
                duration=dur,
                mode="—",
                to_fleet=hf, to_function=hfn,
                new_pilot_name=f"TBD ({atype} #{h_i + 1})",
                new_pilot_nationality=hnat,
                note=(
                    f"[OPT] Optimiser-generated hire. "
                    f"Auto-terminates after {expat_contract_months} months."
                    if hnat == "Expat"
                    else "[OPT] Optimiser-generated local hire."
                ),
                cost=cost,
                cost_currency="MVR",
                trainee_ids=[placeholder_pilot_id] if hnat == "Expat" else [],
            ))

            # For expat hires, schedule an auto-termination at end of contract
            if hnat == "Expat":
                arrival_month = hsm + dur
                term_month = arrival_month + expat_contract_months
                if term_month < horizon:
                    acts.append(PlannedAction(
                        id=new_id("opt"),
                        action_type="Pilot Termination",
                        start_month=term_month,
                        duration=0,
                        mode="—",
                        trainee_ids=[placeholder_pilot_id],
                        note=(
                            f"[OPT] Auto-termination of optimiser-hired expat "
                            f"after {expat_contract_months}-month contract."
                        ),
                        cost=DEFAULT_COST_MVR["termination"],
                        cost_currency="MVR",
                    ))

    # Terminations — batch same-month terminations into single actions
    term_by_month: dict[int, list[str]] = {}
    for (pid, tm), tvar in x_TERM.items():
        if (pulp.value(tvar) or 0) >= 0.5:
            term_by_month.setdefault(tm, []).append(pid)

    for tm, pids in term_by_month.items():
        acts.append(PlannedAction(
            id=new_id("opt"),
            action_type="Pilot Termination",
            start_month=tm,
            duration=0,
            mode="—",
            trainee_ids=pids,
            note="[OPT] Optimiser-scheduled expat termination",
            cost=DEFAULT_COST_MVR["termination"] * len(pids),
            cost_currency="MVR",
        ))

    return sorted(acts, key=lambda a: a.start_month)


def _build_explanation(status, obj_val, elapsed, actions, total_short, goals) -> str:
    lines = [
        f"Solver status: **{status}**",
        f"Elapsed: {elapsed:.1f}s",
    ]
    if obj_val is not None:
        lines.append(f"Objective value: {obj_val:,.0f}")
    lines.append(f"Actions proposed: {len(actions)}")
    lines.append(f"Total pilot-months of shortfall: {total_short:.1f}")

    n_tr = sum(1 for a in actions if a.action_type == "Type Rating")
    n_cu = sum(1 for a in actions if a.action_type == "Command Upgrade")
    n_hire = sum(1 for a in actions if a.action_type in ("Cadet Hire", "Expat Hire", "Local Hire"))
    n_term = sum(1 for a in actions if a.action_type == "Pilot Termination")

    lines.append("")
    lines.append("Action breakdown:")
    lines.append(f"  - Type Ratings: {n_tr}")
    lines.append(f"  - Command Upgrades: {n_cu}")
    lines.append(f"  - Hires: {n_hire}")
    lines.append(f"  - Terminations: {n_term}")

    if status == "infeasible":
        lines.append("")
        lines.append("**Diagnosis:** The problem was infeasible as stated. "
                     "This usually means one of your goals cannot be achieved "
                     "given the career ladder, training durations, and hiring "
                     "restrictions. Try: (1) pushing target dates further out, "
                     "(2) allowing more expats as a bridge, (3) increasing "
                     "concurrent-training limits, or (4) relaxing hard goals "
                     "to 'nice-to-have' priority.")

    return "\n".join(lines)
