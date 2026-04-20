"""
IASL Crew Planning Portal — cascade engine.

Pure-Python planning logic. No Streamlit, no Plotly rendering, no file I/O.
Produces data structures that the UI and PDF layers render consistently.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any
import calendar
import uuid

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FLEETS = ["A330", "A320", "ATR72", "DHC8"]

CREW_SETS_PER_AIRCRAFT = {
    "A330":  7,
    "A320":  5,
    "ATR72": 6,
    "DHC8":  5,
}

DEFAULT_AIRCRAFT_COUNTS = {
    "A330":  1,
    "A320":  1,
    "ATR72": 5,
    "DHC8":  3,
}

FUNCTIONS = ["Captain", "First Officer"]
NATIONALITIES = ["Local", "Expat"]
DESIGNATIONS = ["TRE", "TRI", "LI"]
PILOT_STATUSES = ["Active", "On Type Rating", "On Leave"]

TRAINING_DURATIONS = {
    "type_rating_dhc8_to_atr":        2,
    "type_rating_any_to_a320_fo":     2,
    "type_rating_a320_fo_to_a330_fo": 1,
    "cadet_atr_fo":                   2,
    "command_upgrade_same_fleet":     1,
    "a330_fo_to_a320_captain":        1 + 1,
}

ACTION_TYPES = [
    "Type Rating",
    "Command Upgrade",
    "Cadet Hire",
    "Expat Hire",
    "Local Hire",
    "Fleet Change",
    "Pilot Termination",
]

# Seat Support encoding — trainee_ids entries prefixed with SEAT: are off line
# ops for the duration but do not change fleet/function.
SEAT_PREFIX = "SEAT:"


def _strip_seat(tid: str) -> tuple[bool, str]:
    """Return (is_seat_support, underlying_pilot_id)."""
    if tid.startswith(SEAT_PREFIX):
        return True, tid[len(SEAT_PREFIX):]
    return False, tid


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Pilot:
    employee_id: str
    full_name: str
    nationality: str
    fleet: str
    function: str
    designations: list[str] = field(default_factory=list)
    management: bool = False
    status: str = "Active"

    def contribution(self) -> float:
        if self.status != "Active":
            return 0.0
        return 0.5 if self.management else 1.0


@dataclass
class FleetChange:
    id: str
    fleet: str
    month_index: int
    delta: int
    note: str = ""


@dataclass
class PlannedAction:
    id: str
    action_type: str
    start_month: int
    duration: int
    mode: str = "External"
    instructor_id: str = ""
    trainee_ids: list[str] = field(default_factory=list)
    from_fleet: str = ""
    from_function: str = ""
    to_fleet: str = ""
    to_function: str = ""
    note: str = ""
    new_pilot_name: str = ""
    new_pilot_nationality: str = ""


# ---------------------------------------------------------------------------
# Date / month helpers
# ---------------------------------------------------------------------------
def month_index_to_label(start_year: int, start_month: int, idx: int) -> str:
    y = start_year + (start_month - 1 + idx) // 12
    m = (start_month - 1 + idx) % 12 + 1
    return f"{y}-{calendar.month_abbr[m]}"


def month_labels(start_year: int, start_month: int, horizon: int) -> list[str]:
    return [month_index_to_label(start_year, start_month, i) for i in range(horizon)]


# ---------------------------------------------------------------------------
# Aircraft count resolver
# ---------------------------------------------------------------------------
def resolve_aircraft_counts(
    initial: dict[str, int],
    changes: list[FleetChange],
    horizon: int,
) -> dict[str, list[int]]:
    out = {f: [initial.get(f, 0)] * horizon for f in FLEETS}
    for ch in sorted(changes, key=lambda c: c.month_index):
        if ch.fleet not in out:
            continue
        for i in range(ch.month_index, horizon):
            out[ch.fleet][i] = max(0, out[ch.fleet][i] + ch.delta)
    return out


def fleet_requirement(aircraft_counts: dict[str, list[int]]) -> dict[str, dict[str, list[int]]]:
    req: dict[str, dict[str, list[int]]] = {}
    for fleet, counts in aircraft_counts.items():
        sets = CREW_SETS_PER_AIRCRAFT[fleet]
        per_function = [c * sets for c in counts]
        req[fleet] = {
            "Captain": list(per_function),
            "First Officer": list(per_function),
        }
    return req


# ---------------------------------------------------------------------------
# Availability resolver
# ---------------------------------------------------------------------------
def _action_occupies(action: PlannedAction, month_idx: int) -> bool:
    return action.start_month <= month_idx < action.start_month + action.duration


def _pilot_unavailable_during_action(action: PlannedAction, pilot_id: str) -> bool:
    if action.action_type not in ("Type Rating", "Command Upgrade"):
        return False
    if action.mode == "Internal" and action.instructor_id == pilot_id:
        return True
    if pilot_id in action.trainee_ids:
        return True
    if f"{SEAT_PREFIX}{pilot_id}" in action.trainee_ids:
        return True
    return False


def compute_availability(
    pilots: list[Pilot],
    actions: list[PlannedAction],
    horizon: int,
) -> dict[str, dict[str, list[float]]]:
    avail: dict[str, dict[str, list[float]]] = {
        f: {"Captain": [0.0] * horizon, "First Officer": [0.0] * horizon} for f in FLEETS
    }
    by_id = {p.employee_id: p for p in pilots}

    # Termination month per pilot — contribution drops to zero from this month onward
    terminated_from: dict[str, int] = {}
    for a in actions:
        if a.action_type == "Pilot Termination":
            for tid in a.trainee_ids:
                if tid.startswith("TBD"):
                    continue
                if tid not in terminated_from or a.start_month < terminated_from[tid]:
                    terminated_from[tid] = a.start_month

    for month in range(horizon):
        for p in pilots:
            if not p.fleet or p.fleet not in FLEETS:
                continue
            if p.employee_id in terminated_from and month >= terminated_from[p.employee_id]:
                continue
            c = p.contribution()
            if c == 0:
                continue
            off = False
            for a in actions:
                if _action_occupies(a, month) and _pilot_unavailable_during_action(a, p.employee_id):
                    off = True
                    break
            if off:
                continue
            avail[p.fleet][p.function][month] += c

        for a in actions:
            end = a.start_month + a.duration
            if month < end:
                continue

            if a.action_type == "Type Rating":
                for tid in a.trainee_ids:
                    is_seat, real_id = _strip_seat(tid)
                    if is_seat:
                        continue
                    if real_id.startswith("TBD"):
                        if a.to_fleet in FLEETS and a.to_function in FUNCTIONS:
                            avail[a.to_fleet][a.to_function][month] += 1.0
                    else:
                        p = by_id.get(real_id)
                        if p is None:
                            continue
                        if real_id in terminated_from and month >= terminated_from[real_id]:
                            continue
                        c = 0.5 if p.management else 1.0
                        if p.status != "Active":
                            c = 0.0
                        if p.fleet in FLEETS:
                            avail[p.fleet][p.function][month] -= c
                        if a.to_fleet in FLEETS and a.to_function in FUNCTIONS:
                            avail[a.to_fleet][a.to_function][month] += c

            elif a.action_type == "Command Upgrade":
                for tid in a.trainee_ids:
                    is_seat, real_id = _strip_seat(tid)
                    if is_seat:
                        continue
                    if real_id.startswith("TBD"):
                        if a.to_fleet in FLEETS:
                            avail[a.to_fleet]["Captain"][month] += 1.0
                    else:
                        p = by_id.get(real_id)
                        if p is None:
                            continue
                        if real_id in terminated_from and month >= terminated_from[real_id]:
                            continue
                        c = 0.5 if p.management else 1.0
                        if p.status != "Active":
                            c = 0.0
                        if p.fleet in FLEETS:
                            avail[p.fleet][p.function][month] -= c
                        if a.to_fleet in FLEETS:
                            avail[a.to_fleet]["Captain"][month] += c

            elif a.action_type in ("Cadet Hire", "Expat Hire", "Local Hire"):
                if a.to_fleet in FLEETS and a.to_function in FUNCTIONS:
                    avail[a.to_fleet][a.to_function][month] += 1.0

    for f in avail:
        for fn in avail[f]:
            avail[f][fn] = [max(0.0, v) for v in avail[f][fn]]

    return avail


# ---------------------------------------------------------------------------
# Gap and banding
# ---------------------------------------------------------------------------
def gap_band(gap: float) -> str:
    if gap < 1:
        return "green"
    if gap < 2:
        return "amber"
    return "red"


def compute_gaps(
    requirement: dict[str, dict[str, list[int]]],
    availability: dict[str, dict[str, list[float]]],
) -> dict[str, dict[str, list[float]]]:
    gaps: dict[str, dict[str, list[float]]] = {}
    for f in requirement:
        gaps[f] = {}
        for fn in ("Captain", "First Officer"):
            req = requirement[f][fn]
            avl = availability[f][fn]
            gaps[f][fn] = [max(0.0, req[i] - avl[i]) for i in range(len(req))]
    return gaps


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------
def detect_conflicts(actions: list[PlannedAction]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    pilot_windows: dict[str, list[tuple[PlannedAction, int, int]]] = {}

    for a in actions:
        involved: list[str] = []
        if a.action_type in ("Type Rating", "Command Upgrade"):
            if a.mode == "Internal" and a.instructor_id and not a.instructor_id.startswith("TBD"):
                involved.append(a.instructor_id)
            for tid in a.trainee_ids:
                if not tid:
                    continue
                _is_seat, real_id = _strip_seat(tid)
                if real_id.startswith("TBD"):
                    continue
                involved.append(real_id)

        for pid in involved:
            pilot_windows.setdefault(pid, []).append(
                (a, a.start_month, a.start_month + a.duration)
            )

    for pid, entries in pilot_windows.items():
        entries.sort(key=lambda e: e[1])
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                a1, s1, e1 = entries[i]
                a2, s2, e2 = entries[j]
                if s2 < e1:
                    conflicts.append({
                        "pilot_id": pid,
                        "action_ids": [a1.id, a2.id],
                        "reason": f"{pid} assigned to two overlapping actions "
                                  f"({a1.action_type} & {a2.action_type}).",
                    })
    return conflicts


# ---------------------------------------------------------------------------
# Cascade graph builder
# ---------------------------------------------------------------------------
def build_cascade_graph(
    action: PlannedAction,
    pilots: list[Pilot],
    all_actions: list[PlannedAction],
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    by_id = {p.employee_id: p for p in pilots}

    def add_node(nid, label, kind, month=None):
        nodes.append({"id": nid, "label": label, "kind": kind, "month": month})

    def add_edge(src, tgt, label=""):
        edges.append({"source": src, "target": tgt, "label": label})

    start = action.start_month
    end = action.start_month + action.duration

    if action.action_type == "Command Upgrade":
        add_node("root", f"Command Upgrade\n{action.from_fleet} → {action.to_fleet} CPT",
                 "trigger", start)

        for i, tid in enumerate(action.trainee_ids):
            is_seat, real_id = _strip_seat(tid)
            name = "TBD" if real_id.startswith("TBD") else by_id.get(real_id, Pilot(
                real_id, real_id, "", "", "", [], False, "Active")).full_name

            if is_seat:
                sid = f"seat_{i}"
                add_node(sid, f"{name}\nseat support ({action.duration}mo)\nreturns to origin",
                         "training", start)
                add_edge("root", sid)
                continue

            tn = f"cap_{i}"
            add_node(tn, f"{name}\nnew {action.to_fleet} Captain", "arrival", end)
            add_edge("root", tn, f"+{action.duration}mo training")

            slot_id = f"slot_{i}"
            origin = action.from_fleet or (by_id[real_id].fleet if real_id in by_id else "")
            add_node(slot_id, f"FO slot opens\n{origin}", "slot", start)
            add_edge(tn, slot_id, "vacates FO seat")

            filled = False
            for a2 in all_actions:
                if a2.id == action.id:
                    continue
                if a2.action_type == "Type Rating" and a2.to_fleet == origin and a2.to_function == "First Officer":
                    if a2.start_month >= start:
                        tr_id = f"tr_{i}_{a2.id}"
                        add_node(tr_id, f"Type Rating {a2.from_fleet}→{origin}\n"
                                        f"{a2.duration}mo",
                                 "training", a2.start_month)
                        add_edge(slot_id, tr_id, "feeder move")
                        ar_id = f"ar_{i}_{a2.id}"
                        names = ", ".join(
                            "TBD" if _strip_seat(t)[1].startswith("TBD")
                            else (by_id[_strip_seat(t)[1]].full_name
                                  if _strip_seat(t)[1] in by_id else _strip_seat(t)[1])
                            for t in a2.trainee_ids if not _strip_seat(t)[0]
                        )
                        add_node(ar_id, f"{names}\nactive {origin} FO",
                                 "arrival", a2.start_month + a2.duration)
                        add_edge(tr_id, ar_id, f"arrives {a2.start_month + a2.duration}")
                        filled = True
                        break
                if a2.action_type in ("Cadet Hire", "Local Hire", "Expat Hire") and \
                        a2.to_fleet == origin and a2.to_function == "First Officer":
                    if a2.start_month >= start:
                        tr_id = f"hr_{i}_{a2.id}"
                        add_node(tr_id, f"{a2.action_type}\n{a2.duration}mo type rating",
                                 "training", a2.start_month)
                        add_edge(slot_id, tr_id, "new hire")
                        ar_id = f"ha_{i}_{a2.id}"
                        add_node(ar_id, f"{a2.new_pilot_name or 'TBD'}\nactive {origin} FO",
                                 "arrival", a2.start_month + a2.duration)
                        add_edge(tr_id, ar_id, f"arrives {a2.start_month + a2.duration}")
                        filled = True
                        break
            if not filled:
                gap_id = f"gap_{i}"
                add_node(gap_id, "UNFILLED — plan a feeder\nor hire", "note", start)
                add_edge(slot_id, gap_id, "no downstream plan")

    elif action.action_type == "Type Rating":
        add_node("root", f"Type Rating\n{action.from_fleet} {action.from_function} → "
                         f"{action.to_fleet} {action.to_function}", "trigger", start)
        for i, tid in enumerate(action.trainee_ids):
            is_seat, real_id = _strip_seat(tid)
            name = "TBD" if real_id.startswith("TBD") else by_id.get(real_id, Pilot(
                real_id, real_id, "", "", "", [], False, "Active")).full_name

            tr_id = f"tr_{i}"
            label = f"{name}\n{'seat support' if is_seat else 'in training'} ({action.duration}mo)"
            add_node(tr_id, label, "training", start)
            add_edge("root", tr_id)

            if is_seat:
                back_id = f"back_{i}"
                if real_id in by_id:
                    p = by_id[real_id]
                    back_label = f"{name}\nreturns to {p.fleet} {p.function}"
                else:
                    back_label = f"{name}\nreturns to original role"
                add_node(back_id, back_label, "arrival", end)
                add_edge(tr_id, back_id, f"returns {end}")
            else:
                ar_id = f"ar_{i}"
                add_node(ar_id, f"{name}\nactive {action.to_fleet} {action.to_function}",
                         "arrival", end)
                add_edge(tr_id, ar_id, f"arrives {end}")
                if action.from_fleet and action.from_function:
                    slot_id = f"slot_{i}"
                    add_node(slot_id, f"{action.from_fleet} {action.from_function}\nslot opens",
                             "slot", start)
                    add_edge(tr_id, slot_id, "vacates origin")

    elif action.action_type in ("Cadet Hire", "Local Hire", "Expat Hire"):
        add_node("root", f"{action.action_type}\n{action.new_pilot_name or 'TBD'}",
                 "trigger", start)
        tr_id = "tr"
        add_node(tr_id,
                 f"Type rating ({action.duration}mo)" if action.duration > 0
                 else "No training lag",
                 "training", start)
        add_edge("root", tr_id)
        ar_id = "ar"
        add_node(ar_id, f"Active {action.to_fleet} {action.to_function}",
                 "arrival", end)
        add_edge(tr_id, ar_id, f"arrives {end}")

    elif action.action_type == "Fleet Change":
        add_node("root", f"Fleet Change\n{action.from_fleet}: {action.note or ''}",
                 "trigger", start)
        add_node("impact", "Requirement recalculates\nfrom this month", "note", start)
        add_edge("root", "impact")

    elif action.action_type == "Pilot Termination":
        add_node("root", f"Pilot Termination\n{len(action.trainee_ids)} pilot(s)",
                 "trigger", start)
        for i, tid in enumerate(action.trainee_ids):
            if tid.startswith("TBD"):
                name = tid
                origin = "TBD"
                p = None
            else:
                p = by_id.get(tid)
                name = p.full_name if p else tid
                origin = f"{p.fleet} {p.function}" if p else "unknown"
            t_id = f"term_{i}"
            add_node(t_id, f"{name}\ndeparts from {origin}", "note", start)
            add_edge("root", t_id, "removed from roster")
            if p and p.fleet in FLEETS:
                slot_id = f"slot_{i}"
                add_node(slot_id, f"{p.fleet} {p.function}\nslot opens",
                         "slot", start)
                add_edge(t_id, slot_id)

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Localisation analysis
# ---------------------------------------------------------------------------
def eligible_feeders_for(
    expat: Pilot,
    pilots: list[Pilot],
    actions: list[PlannedAction] | None = None,
) -> list[dict[str, Any]]:
    terminated: set[str] = set()
    if actions:
        for a in actions:
            if a.action_type == "Pilot Termination":
                terminated.update(t for t in a.trainee_ids if not t.startswith("TBD"))

    candidates: list[dict[str, Any]] = []
    target_fleet = expat.fleet
    target_function = expat.function

    for p in pilots:
        if p.nationality != "Local":
            continue
        if p.status != "Active":
            continue
        if p.employee_id == expat.employee_id:
            continue
        if p.employee_id in terminated:
            continue
        route, duration = _training_route(p, target_fleet, target_function)
        if route is None:
            continue
        candidates.append({
            "pilot_id": p.employee_id,
            "pilot_name": p.full_name,
            "from": f"{p.fleet} {p.function}",
            "to": f"{target_fleet} {target_function}",
            "route": route,
            "duration_months": duration,
        })
    candidates.sort(key=lambda c: c["duration_months"])
    return candidates


def _training_route(pilot: Pilot, to_fleet: str, to_function: str) -> tuple[str | None, int]:
    f, fn = pilot.fleet, pilot.function

    if f == to_fleet and fn == to_function:
        return None, 0

    if f == to_fleet and fn == "First Officer" and to_function == "Captain":
        return f"Command Upgrade on {f}", TRAINING_DURATIONS["command_upgrade_same_fleet"]

    if f == "DHC8" and to_fleet == "ATR72" and fn == to_function:
        return "Type Rating DHC-8 → ATR72", TRAINING_DURATIONS["type_rating_dhc8_to_atr"]

    if f in ("ATR72", "DHC8") and to_fleet == "A320" and to_function == "First Officer":
        return f"Type Rating {f} → A320 FO", TRAINING_DURATIONS["type_rating_any_to_a320_fo"]

    if f == "A320" and fn == "First Officer" and to_fleet == "A330" and to_function == "First Officer":
        return "Type Rating A320 FO → A330 FO", TRAINING_DURATIONS["type_rating_a320_fo_to_a330_fo"]

    if f == "A330" and fn == "First Officer" and to_fleet == "A320" and to_function == "Captain":
        return "Type Rating A330 FO → A320 FO + Command Upgrade", TRAINING_DURATIONS["a330_fo_to_a320_captain"]

    if f == "A320" and fn == "Captain" and to_fleet == "A330" and to_function == "Captain":
        return "Command Upgrade path (A320 CPT candidate for A330 CPT)", 1

    return None, 0


def localisation_summary(pilots: list[Pilot]) -> dict[str, Any]:
    by_fleet: dict[str, dict[str, int]] = {}
    for f in FLEETS:
        by_fleet[f] = {"total": 0, "local": 0, "expat": 0}
    for p in pilots:
        if p.fleet not in FLEETS:
            continue
        by_fleet[p.fleet]["total"] += 1
        if p.nationality == "Local":
            by_fleet[p.fleet]["local"] += 1
        elif p.nationality == "Expat":
            by_fleet[p.fleet]["expat"] += 1

    total = sum(v["total"] for v in by_fleet.values())
    local = sum(v["local"] for v in by_fleet.values())
    expat = sum(v["expat"] for v in by_fleet.values())
    pct = (local / total * 100) if total > 0 else 0.0
    return {
        "by_fleet": by_fleet,
        "total": total,
        "local": local,
        "expat": expat,
        "local_pct": pct,
    }


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 1


def serialise_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": date.today().isoformat(),
        "start_year": state["start_year"],
        "start_month": state["start_month"],
        "horizon": state["horizon"],
        "initial_aircraft": state["initial_aircraft"],
        "pilots": [asdict(p) for p in state["pilots"]],
        "fleet_changes": [asdict(c) for c in state["fleet_changes"]],
        "actions": [asdict(a) for a in state["actions"]],
    }


def deserialise_state(payload: dict[str, Any]) -> dict[str, Any]:
    v = payload.get("schema_version", 0)
    if v != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema version {v}. Expected {SCHEMA_VERSION}.")
    return {
        "start_year": int(payload["start_year"]),
        "start_month": int(payload["start_month"]),
        "horizon": int(payload["horizon"]),
        "initial_aircraft": {k: int(v) for k, v in payload["initial_aircraft"].items()},
        "pilots": [Pilot(**p) for p in payload["pilots"]],
        "fleet_changes": [FleetChange(**c) for c in payload["fleet_changes"]],
        "actions": [PlannedAction(**a) for a in payload["actions"]],
    }


def new_id(prefix: str = "id") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
