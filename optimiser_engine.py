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
    strategy: str = "cost"
    # Window of months the solver may plan within. If end < 0, defaults to horizon-1.
    window_start_month: int = 0
    window_end_month: int = -1
    # If True, monthly requirements must be met at every month in the window.
    # If False, only goals' target_month need be met; intermediate shortfalls
    # are penalised softly but not forbidden.
    enforce_monthly_requirements: bool = True
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

    Respects the existing action list as an immutable set of committed moves.
    Plans only within [window_start_month, window_end_month].

    Returns an OptimiserResult with the NEW recommended actions only.
    Applying the result appends to the existing action list — nothing is
    replaced.
    """
    import pulp

    horizon = state["horizon"]
    pilots: list[Pilot] = state["pilots"]
    existing_actions: list[PlannedAction] = list(state["actions"])
    ac_counts = resolve_aircraft_counts(
        state["initial_aircraft"], state["fleet_changes"], horizon)
    requirement = fleet_requirement(ac_counts)
    labels = month_labels(state["start_year"], state["start_month"], horizon)

    # Normalise window
    w_start = max(0, config.window_start_month)
    w_end = config.window_end_month if config.window_end_month >= 0 else horizon - 1
    w_end = min(w_end, horizon - 1)
    if w_end < w_start:
        return OptimiserResult(
            status="error",
            objective_value=None,
            gap_percent=None,
            elapsed_seconds=0,
            actions=[],
            per_month_shortfall={},
            explanation=(
                f"Invalid window: end month ({w_end}) is before start ({w_start})."
            ),
        )

    t_start = time.time()

    def _progress(phase, msg="", inc=None, bound=None, gap=None):
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

    _progress("setup", f"Window: {labels[w_start]} → {labels[w_end]} "
                      f"({w_end - w_start + 1} months)")

    # ------------------------------------------------------------------
    # Compute baseline availability from EXISTING state + existing actions
    # This is what the window "inherits" at w_start and forward.
    # ------------------------------------------------------------------
    _progress("setup", "Computing baseline availability from existing actions…")
    baseline_availability = compute_availability(pilots, existing_actions, horizon)

    # Determine which pilots are "busy" during the window due to existing
    # actions (training, command upgrade) — they are not candidates for
    # new solver-proposed actions during those months
    pilot_busy_until: dict[str, int] = {}
    for a in existing_actions:
        if a.action_type in ("Type Rating", "Command Upgrade"):
            busy_end = a.start_month + a.duration
            for tid in a.trainee_ids:
                if tid.startswith("SEAT:"):
                    real = tid[len("SEAT:"):]
                    pilot_busy_until[real] = max(pilot_busy_until.get(real, 0), busy_end)
                elif not tid.startswith("TBD"):
                    pilot_busy_until[tid] = max(pilot_busy_until.get(tid, 0), busy_end)

    # Determine which pilots have been "moved" by existing actions — their
    # effective (fleet, function) at any month m is what the cascade engine
    # ended up calling their position. For the solver, treat their position
    # at w_start as their starting point (so a pilot who already completed a
    # type rating before w_start is in their new role, not old).
    effective_position_at_window: dict[str, tuple[str, str]] = {}
    for p in pilots:
        if p.fleet not in FLEETS:
            continue
        fleet, func = p.fleet, p.function
        for a in sorted(existing_actions, key=lambda x: x.start_month):
            if a.action_type == "Pilot Termination":
                continue
            end = a.start_month + a.duration
            if end > w_start:
                continue
            # Pilot is a trainee (not seat support)
            is_trainee = (p.employee_id in a.trainee_ids
                          and f"SEAT:{p.employee_id}" not in a.trainee_ids)
            if not is_trainee:
                continue
            if a.action_type == "Type Rating":
                fleet, func = a.to_fleet, a.to_function
            elif a.action_type == "Command Upgrade":
                fleet, func = a.to_fleet, "Captain"
        effective_position_at_window[p.employee_id] = (fleet, func)

    # Pilots already terminated before w_start — excluded entirely
    terminated_before_window: set[str] = set()
    for a in existing_actions:
        if a.action_type == "Pilot Termination" and a.start_month <= w_start:
            for tid in a.trainee_ids:
                if not tid.startswith("TBD"):
                    terminated_before_window.add(tid)

    # Active local pilots eligible for solver-proposed transitions:
    # - not terminated before window
    # - at their effective position, still active
    # - not busy with an existing training that extends into the window
    local_pilots: list[Pilot] = []
    for p in pilots:
        if p.nationality != "Local":
            continue
        if p.fleet not in FLEETS:
            continue
        if p.employee_id in terminated_before_window:
            continue
        if p.status != "Active":
            continue
        # If busy through the entire window, exclude
        busy_until = pilot_busy_until.get(p.employee_id, 0)
        if busy_until > w_end:
            continue
        # Clone pilot with effective position at window start
        eff = effective_position_at_window.get(p.employee_id, (p.fleet, p.function))
        if eff[0] not in FLEETS:
            continue
        local_pilots.append(Pilot(
            employee_id=p.employee_id, full_name=p.full_name,
            nationality=p.nationality, fleet=eff[0], function=eff[1],
            designations=p.designations, management=p.management,
            status=p.status,
        ))

    # Active expats eligible for solver-proposed termination — only those
    # who are active at w_start and not already scheduled for termination
    scheduled_term_existing: set[str] = set()
    for a in existing_actions:
        if a.action_type == "Pilot Termination":
            for tid in a.trainee_ids:
                if not tid.startswith("TBD"):
                    scheduled_term_existing.add(tid)

    expat_pilots: list[Pilot] = []
    for p in pilots:
        if p.nationality != "Expat":
            continue
        if p.fleet not in FLEETS:
            continue
        if p.employee_id in terminated_before_window:
            continue
        if p.employee_id in scheduled_term_existing:
            continue
        if p.status != "Active":
            continue
        eff = effective_position_at_window.get(p.employee_id, (p.fleet, p.function))
        expat_pilots.append(Pilot(
            employee_id=p.employee_id, full_name=p.full_name,
            nationality=p.nationality, fleet=eff[0], function=eff[1],
            designations=p.designations, management=p.management,
            status=p.status,
        ))

    _progress("setup",
              f"Eligible local candidates: {len(local_pilots)}, "
              f"expat candidates for termination: {len(expat_pilots)}")

    # Career transitions available per pilot
    pilot_transitions: dict[str, list[tuple[str, str, str, int]]] = {}
    for p in local_pilots:
        pilot_transitions[p.employee_id] = _pilot_eligible_transitions(p)

    # Valid start months: action must fit within the window
    def _valid_starts(duration: int) -> list[int]:
        earliest = w_start
        # An action starting at sm occupies months sm..sm+duration-1, plus
        # pilot is unavailable until sm+duration. The action must complete
        # by w_end+1 to be effective.
        latest = w_end - duration + 1
        if latest < earliest:
            return []
        return list(range(earliest, latest + 1))

    # ------------------------------------------------------------------
    # DECISION VARIABLES — only new actions in the window
    # ------------------------------------------------------------------
    prob = pulp.LpProblem("IASL_Crew_Optimiser", pulp.LpMinimize)

    x_TR: dict[tuple[str, str, str, int], pulp.LpVariable] = {}
    for p in local_pilots:
        for (tf, tfn, atype, dur) in pilot_transitions[p.employee_id]:
            for sm in _valid_starts(dur):
                # Respect pilot_busy_until — don't start while still in existing training
                busy = pilot_busy_until.get(p.employee_id, 0)
                if sm < busy:
                    continue
                key = (p.employee_id, tf, tfn, sm)
                x_TR[key] = pulp.LpVariable(
                    f"x_tr_{p.employee_id}_{tf}_{tfn.replace(' ','_')}_{sm}",
                    cat="Binary",
                )

    x_HIRE: dict[tuple[str, str, str, int], pulp.LpVariable] = {}
    for f in FLEETS:
        for fn in FUNCTIONS:
            for nat in ("Local", "Expat"):
                if nat == "Local" and not (f == "ATR72" and fn == "First Officer"):
                    continue
                if nat == "Expat" and not config.allow_expat_hires:
                    continue
                for sm in range(w_start, w_end + 1):
                    training_dur = TRAINING_DURATIONS["cadet_atr_fo"] if nat == "Local" else 0
                    if sm + training_dur > w_end + 1:
                        continue
                    key = (f, fn, nat, sm)
                    x_HIRE[key] = pulp.LpVariable(
                        f"x_hire_{f}_{fn.replace(' ','_')}_{nat}_{sm}",
                        lowBound=0, upBound=20, cat="Integer",
                    )

    x_TERM: dict[tuple[str, int], pulp.LpVariable] = {}
    if config.allow_terminations:
        for p in expat_pilots:
            for m in range(w_start, w_end + 1):
                key = (p.employee_id, m)
                x_TERM[key] = pulp.LpVariable(
                    f"x_term_{p.employee_id}_{m}",
                    cat="Binary",
                )

    # Shortfall slack: one per (fleet, function, month) inside window
    short: dict[tuple[str, str, int], pulp.LpVariable] = {}
    for f in FLEETS:
        for fn in FUNCTIONS:
            for m in range(w_start, w_end + 1):
                short[(f, fn, m)] = pulp.LpVariable(
                    f"short_{f}_{fn.replace(' ','_')}_{m}",
                    lowBound=0, cat="Continuous",
                )

    goal_miss: dict[int, pulp.LpVariable] = {}
    for i, g in enumerate(goals):
        if w_start <= g.target_month <= w_end:
            goal_miss[i] = pulp.LpVariable(
                f"goal_miss_{i}", lowBound=0, cat="Continuous",
            )

    _progress("setup", f"Variables: {len(x_TR)} TR, {len(x_HIRE)} HIRE, "
                      f"{len(x_TERM)} TERM")

    # ------------------------------------------------------------------
    # CONSTRAINTS
    # ------------------------------------------------------------------

    # C1: each local can make at most one new transition within the window
    for p in local_pilots:
        relevant = [x_TR[k] for k in x_TR if k[0] == p.employee_id]
        if relevant:
            prob += pulp.lpSum(relevant) <= 1, f"one_transition_{p.employee_id}"

    # C2: each expat can be terminated at most once
    for p in expat_pilots:
        relevant = [x_TERM[k] for k in x_TERM if k[0] == p.employee_id]
        if relevant:
            prob += pulp.lpSum(relevant) <= 1, f"one_term_{p.employee_id}"

    # C3: max concurrent trainings per fleet per month, INCLUDING the
    # effect of pre-existing in-window training actions
    for f in FLEETS:
        for m in range(w_start, w_end + 1):
            # Count existing actions active at m
            existing_active = 0
            for a in existing_actions:
                if a.action_type not in ("Type Rating", "Command Upgrade"):
                    continue
                a_dur = a.duration or 1
                if a.to_fleet == f and a.start_month <= m < a.start_month + a_dur:
                    # count one per trainee
                    existing_active += sum(
                        1 for t in a.trainee_ids if not t.startswith("SEAT:")
                    )

            # Plus new solver actions active at m
            new_active = []
            for (pid, tf, tfn, sm), var in x_TR.items():
                p = next((pl for pl in local_pilots
                          if pl.employee_id == pid), None)
                if not p: continue
                dur = _transition_duration(p.fleet, p.function, tf, tfn)
                if dur is None: continue
                if tf == f and sm <= m < sm + dur:
                    new_active.append(var)

            if new_active or existing_active > 0:
                budget = max(0,
                             config.max_concurrent_trainings_per_fleet
                             - existing_active)
                prob += pulp.lpSum(new_active) <= budget, \
                    f"concurrent_{f}_{m}"

    # C4: headcount constraint at every month in window
    #     availability(f, fn, m) must be >= requirement(f, fn, m) - short
    # where availability is:
    #   baseline_availability[f][fn][m]  -- reflects full existing plan
    #   + new transitions completed by m
    #   - new transitions that pulled pilot out of (f, fn) by m
    #   + new hires arrived by m
    #   - new terminations active by m  (for expats in (f, fn))

    # Precompute per-pilot effective (fleet, function) so we know where
    # outflows come from
    pilot_by_id_local = {p.employee_id: p for p in local_pilots}
    pilot_by_id_expat = {p.employee_id: p for p in expat_pilots}

    for f in FLEETS:
        for fn in FUNCTIONS:
            for m in range(w_start, w_end + 1):
                # Outflows from (f, fn) due to new solver transitions
                outflows = []
                for (pid, tf, tfn, sm), var in x_TR.items():
                    p = pilot_by_id_local.get(pid)
                    if not p: continue
                    if p.fleet == f and p.function == fn and sm <= m:
                        outflows.append(var)

                # Inflows to (f, fn) due to new solver transitions completed
                inflows = []
                for (pid, tf, tfn, sm), var in x_TR.items():
                    if tf != f or tfn != fn:
                        continue
                    p = pilot_by_id_local.get(pid)
                    if not p: continue
                    dur = _transition_duration(p.fleet, p.function, tf, tfn)
                    if dur is None: continue
                    if sm + dur <= m:
                        inflows.append(var)

                # Inflows from new solver hires
                for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
                    if hf != f or hfn != fn: continue
                    training_dur = (TRAINING_DURATIONS["cadet_atr_fo"]
                                    if hnat == "Local" else 0)
                    if hsm + training_dur <= m:
                        inflows.append(hvar)

                # Outflows from (f, fn) due to new expat terminations
                term_outflows = []
                for (pid, tm), tvar in x_TERM.items():
                    p = pilot_by_id_expat.get(pid)
                    if not p: continue
                    if p.fleet == f and p.function == fn and tm <= m:
                        term_outflows.append(tvar)

                # Constraint
                base = baseline_availability[f][fn][m]
                req_m = requirement[f][fn][m]

                if config.enforce_monthly_requirements:
                    # Hard enforcement with shortfall slack
                    prob += (
                        base
                        + pulp.lpSum(inflows)
                        - pulp.lpSum(outflows)
                        - pulp.lpSum(term_outflows)
                        + short[(f, fn, m)]
                        >= req_m
                    ), f"hc_{f}_{fn.replace(' ','_')}_{m}"
                else:
                    # Soft: only track shortfall, don't require >= req
                    # Still write the identity for the solver to compute slack correctly
                    prob += (
                        base
                        + pulp.lpSum(inflows)
                        - pulp.lpSum(outflows)
                        - pulp.lpSum(term_outflows)
                        + short[(f, fn, m)]
                        >= req_m - 1e6  # effectively free
                    ), f"hc_soft_{f}_{fn.replace(' ','_')}_{m}"

    # C5: user goals — hard for "must", soft for "nice"
    for gi, g in enumerate(goals):
        m = g.target_month
        if not (w_start <= m <= w_end):
            continue

        base_f_fn = baseline_availability[g.fleet][g.function][m]
        # Decompose into local vs expat components
        local_base = 0
        for p in pilots:
            if p.fleet == g.fleet and p.function == g.function \
                    and p.nationality == "Local" and p.status == "Active":
                if p.employee_id not in terminated_before_window:
                    eff = effective_position_at_window.get(
                        p.employee_id, (p.fleet, p.function))
                    # Check if this pilot is still in (g.fleet, g.function) at m
                    # after existing actions — approximated by noting the base
                    # already includes them via compute_availability.
                    pass
        # The decomposition between local and expat counts needs a per-nat
        # baseline. We'll compute it directly using a lightweight snapshot.
        local_base, expat_base = _baseline_nat_count_at_month(
            pilots, existing_actions, g.fleet, g.function, m, horizon
        )

        local_in = []
        local_out = []
        for (pid, tf, tfn, sm), var in x_TR.items():
            p = pilot_by_id_local.get(pid)
            if not p: continue
            dur = _transition_duration(p.fleet, p.function, tf, tfn)
            if dur is None: continue
            if tf == g.fleet and tfn == g.function and sm + dur <= m:
                local_in.append(var)
            if p.fleet == g.fleet and p.function == g.function and sm <= m:
                local_out.append(var)

        for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
            if hf == g.fleet and hfn == g.function and hnat == "Local":
                if hsm + TRAINING_DURATIONS["cadet_atr_fo"] <= m:
                    local_in.append(hvar)

        expat_in = []
        expat_out = []
        for (pid, tm), tvar in x_TERM.items():
            p = pilot_by_id_expat.get(pid)
            if not p: continue
            if p.fleet == g.fleet and p.function == g.function and tm <= m:
                expat_out.append(tvar)
        for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
            if hf == g.fleet and hfn == g.function and hnat == "Expat" and hsm <= m:
                expat_in.append(hvar)

        local_count = local_base + pulp.lpSum(local_in) - pulp.lpSum(local_out)
        expat_count = expat_base + pulp.lpSum(expat_in) - pulp.lpSum(expat_out)
        total_count = local_count + expat_count

        if g.priority == "must":
            prob += total_count >= g.target_total, f"goal_total_{gi}"
            prob += local_count >= g.min_locals, f"goal_min_locals_{gi}"
            prob += expat_count <= g.max_expats, f"goal_max_expats_{gi}"
        else:
            if gi in goal_miss:
                prob += total_count + goal_miss[gi] >= g.target_total, \
                    f"goal_total_{gi}"
            prob += local_count >= g.min_locals, f"goal_min_locals_{gi}"
            prob += expat_count <= g.max_expats, f"goal_max_expats_{gi}"

    # ------------------------------------------------------------------
    # OBJECTIVE
    # ------------------------------------------------------------------
    obj_cost_terms = []
    for (pid, tf, tfn, sm), var in x_TR.items():
        p = pilot_by_id_local.get(pid)
        if not p: continue
        transition_type = _transition_action_type(p.fleet, p.function, tf, tfn)
        cost = _action_cost_mvr(transition_type, from_fleet=p.fleet, to_fleet=tf,
                                training_mode="External")
        obj_cost_terms.append(cost * var)

    for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
        cost = (DEFAULT_COST_MVR["cadet_hire"] if hnat == "Local"
                else DEFAULT_COST_MVR["expat_hire"])
        obj_cost_terms.append(cost * hvar)

    for (pid, tm), tvar in x_TERM.items():
        obj_cost_terms.append(DEFAULT_COST_MVR["termination"] * tvar)

    # Expat-months savings: reward earlier terminations
    obj_expat_terms = []
    for p in expat_pilots:
        salary = EXPAT_MONTHLY_MVR.get((p.fleet, p.function), 0)
        for (pid, tm), tvar in x_TERM.items():
            if pid != p.employee_id: continue
            # Months saved = w_end - tm + 1 (this many months less of expat salary)
            months_saved = (w_end - tm + 1)
            if months_saved > 0:
                obj_expat_terms.append(-salary * months_saved * tvar
                                       * (weights.expat_months / 1000.0))

    obj_short_terms = []
    for key, var in short.items():
        obj_short_terms.append(weights.shortfall * var)

    obj_goal_terms = []
    for gi, var in goal_miss.items():
        if goals[gi].priority == "nice":
            obj_goal_terms.append(weights.shortfall * var)

    obj_local_terms = []
    for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
        if hnat == "Local":
            obj_local_terms.append(weights.local_added * hvar)
    for (pid, tf, tfn, sm), var in x_TR.items():
        obj_local_terms.append(weights.local_added * var * 0.3)

    obj_expat_hire_terms = []
    for (hf, hfn, hnat, hsm), hvar in x_HIRE.items():
        if hnat == "Expat":
            obj_expat_hire_terms.append(weights.expat_hire_penalty * hvar)

    obj_time_terms = []
    if weights.time_to_target > 0:
        window_len = max(1, w_end - w_start + 1)
        for (f, fn, m), svar in short.items():
            month_weight = (m - w_start + 1) / window_len
            obj_time_terms.append(weights.time_to_target * month_weight * svar)

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
    _progress("solving", f"Starting CBC (time limit {config.time_limit_seconds}s)…")
    solver = pulp.PULP_CBC_CMD(
        timeLimit=config.time_limit_seconds,
        msg=False,
        gapRel=0.05,
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

    status_map = {1: "optimal", 0: "not_solved", -1: "infeasible",
                  -2: "unbounded", -3: "undefined"}
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
            explanation=(
                f"Solver reported {status_str}. "
                "Goals may be infeasible within the specified window. Try: "
                "(1) extending the window, (2) relaxing targets, "
                "(3) turning off 'enforce monthly requirements', "
                "(4) allowing more expats, (5) raising concurrent training limit."
            ),
        )

    actions = _extract_actions(
        x_TR, x_HIRE, x_TERM, local_pilots, expat_pilots,
        expat_contract_months=config.expat_hire_contract_months,
        horizon=horizon,
    )

    per_month_short: dict[tuple[str, str], list[float]] = {}
    for f in FLEETS:
        for fn in FUNCTIONS:
            series = []
            for m in range(horizon):
                if w_start <= m <= w_end:
                    v = pulp.value(short[(f, fn, m)]) or 0
                else:
                    v = 0
                series.append(v)
            per_month_short[(f, fn)] = series

    total_short = sum(sum(s) for s in per_month_short.values())

    _progress("done", f"Solver finished in {elapsed:.1f}s with status {status_str}")

    explanation = _build_explanation(
        status_str, obj_val, elapsed, actions, total_short, goals,
        window_start=labels[w_start], window_end=labels[w_end],
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
            "window_start_label": labels[w_start],
            "window_end_label": labels[w_end],
            "window_months": w_end - w_start + 1,
            "eligible_locals": len(local_pilots),
            "eligible_expats_for_termination": len(expat_pilots),
        },
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

def _baseline_nat_count_at_month(
    pilots: list[Pilot],
    existing_actions: list[PlannedAction],
    fleet: str,
    function: str,
    month: int,
    horizon: int,
) -> tuple[int, int]:
    """
    Return (local_count, expat_count) at (fleet, function, month) after all
    existing actions are applied. Used as the baseline for goal constraints.
    """
    # Build terminated-at lookup
    terminated_at: dict[str, int] = {}
    for a in existing_actions:
        if a.action_type == "Pilot Termination":
            for tid in a.trainee_ids:
                if not tid.startswith("TBD"):
                    terminated_at.setdefault(tid, a.start_month)

    # Virtual hires arriving by `month`
    virtual_hires: list[tuple[str, str, str]] = []
    for a in existing_actions:
        if a.action_type in ("Cadet Hire", "Local Hire", "Expat Hire"):
            end = a.start_month + a.duration
            if end <= month:
                nat = "Local" if a.action_type in ("Cadet Hire", "Local Hire") else "Expat"
                virtual_hires.append((a.to_fleet, a.to_function, nat))

    local_count = 0
    expat_count = 0

    for p in pilots:
        if p.fleet not in FLEETS:
            continue
        if p.status != "Active":
            continue
        if p.employee_id in terminated_at and month >= terminated_at[p.employee_id]:
            continue
        # Apply existing transitions up through month
        cur_fleet, cur_fn = p.fleet, p.function
        for a in sorted(existing_actions, key=lambda x: x.start_month):
            if a.action_type == "Pilot Termination":
                continue
            end = a.start_month + a.duration
            if end > month:
                continue
            if p.employee_id not in a.trainee_ids:
                continue
            if f"SEAT:{p.employee_id}" in a.trainee_ids:
                continue
            if a.action_type == "Type Rating":
                cur_fleet, cur_fn = a.to_fleet, a.to_function
            elif a.action_type == "Command Upgrade":
                cur_fleet, cur_fn = a.to_fleet, "Captain"
        if cur_fleet == fleet and cur_fn == function:
            if p.nationality == "Local":
                local_count += 1
            elif p.nationality == "Expat":
                expat_count += 1

    for vh_fleet, vh_fn, vh_nat in virtual_hires:
        if vh_fleet == fleet and vh_fn == function:
            if vh_nat == "Local":
                local_count += 1
            else:
                expat_count += 1

    return local_count, expat_count


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


def _build_explanation(status, obj_val, elapsed, actions, total_short, goals,
                       window_start=None, window_end=None) -> str:
    lines = [
        f"Solver status: **{status}**",
        f"Elapsed: {elapsed:.1f}s",
    ]
    if window_start and window_end:
        lines.append(f"Window: **{window_start} → {window_end}**")
    if obj_val is not None:
        lines.append(f"Objective value: {obj_val:,.0f}")
    lines.append(f"Actions proposed: {len(actions)}")
    lines.append(f"Total pilot-months of shortfall: {total_short:.1f}")

    n_tr = sum(1 for a in actions if a.action_type == "Type Rating")
    n_cu = sum(1 for a in actions if a.action_type == "Command Upgrade")
    n_hire = sum(1 for a in actions
                 if a.action_type in ("Cadet Hire", "Expat Hire", "Local Hire"))
    n_term = sum(1 for a in actions if a.action_type == "Pilot Termination")

    lines.append("")
    lines.append("Action breakdown:")
    lines.append(f"  - Type Ratings: {n_tr}")
    lines.append(f"  - Command Upgrades: {n_cu}")
    lines.append(f"  - Hires: {n_hire}")
    lines.append(f"  - Terminations: {n_term}")

    if status == "infeasible":
        lines.append("")
        lines.append(
            "**Diagnosis:** The problem is infeasible as stated. Common causes: "
            "(a) the window is too short for the training duration required to "
            "meet the goal, (b) 'enforce monthly requirements' is on but a "
            "mid-window shortfall is unavoidable given existing actions, "
            "(c) goal constraints conflict (e.g., max_expats lower than current "
            "expat count with no allowed terminations). Try: extending the "
            "window end, loosening goal priority to 'nice', turning off monthly "
            "enforcement, or allowing terminations."
        )

    return "\n".join(lines)


def preview_window_state(state: dict, window_start_month: int) -> dict:
    """
    Return a human-friendly summary of the crew state at the start of the
    solver window — i.e., what the solver will treat as the starting point.
    Used by the UI to show "this is what you're optimising from".
    """
    pilots: list[Pilot] = state["pilots"]
    existing_actions: list[PlannedAction] = state["actions"]
    horizon = state["horizon"]

    ac_counts = resolve_aircraft_counts(
        state["initial_aircraft"], state["fleet_changes"], horizon)
    requirement = fleet_requirement(ac_counts)
    availability = compute_availability(pilots, existing_actions, horizon)

    summary = {}
    m = max(0, min(window_start_month, horizon - 1))
    for f in FLEETS:
        summary[f] = {}
        for fn in FUNCTIONS:
            local_count, expat_count = _baseline_nat_count_at_month(
                pilots, existing_actions, f, fn, m, horizon)
            req = requirement[f][fn][m]
            avl = availability[f][fn][m]
            summary[f][fn] = {
                "local": local_count,
                "expat": expat_count,
                "total": local_count + expat_count,
                "availability": avl,
                "requirement": req,
                "gap": max(0, req - avl),
            }
    return summary
