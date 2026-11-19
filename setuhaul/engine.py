"""Allocation engine: YAML hard constraints, then a transparent score.

The engine reads STRUCTURED data only (never free text). It answers:
  - which options are feasible for this truck, ranked, with reasons why;
  - or "nothing fits", with the reasons grouped by constraint.
The LLM never decides any of this.
"""
from __future__ import annotations

import functools
import hashlib
import sqlite3
from datetime import datetime, timedelta

import yaml

from . import config
from .clock import at_local, hhmm, iso, parse_ts
from .models import DriverFacts, EtaView, Option, OptionsResult

ACTIVE_APPT = ("PENDING_CONFIRMATION", "CONFIRMED", "IN_PROGRESS")


@functools.lru_cache(maxsize=1)
def rules() -> dict:
    return yaml.safe_load(config.RULES_FILE.read_text())


def option_id_for(slot_ids: list[str]) -> str:
    """Stable id: the same slots always give the same option id."""
    return "OPT-" + hashlib.sha1("|".join(slot_ids).encode()).hexdigest()[:8]


def get_shipment(conn, shipment_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM shipments WHERE shipment_id=?", (shipment_id,)).fetchone()


def required_dock_type(ship) -> str:
    """RULE003 reefer -> REEFER; RULE004 > 25,000 kg -> HEAVY; ANY -> STANDARD."""
    hc = rules()["hard_constraints"]
    if ship["temperature_control_required"] or ship["required_dock_type"] == "REEFER":
        return "REEFER"
    if ship["load_weight_kg"] > hc["heavy_threshold_kg"] or ship["required_dock_type"] == "HEAVY":
        return "HEAVY"
    return "STANDARD"


def _slots_with_docks(conn, slot_ids: list[str]) -> list[sqlite3.Row]:
    q = ("SELECT sl.*, d.dock_code, d.dock_type, d.max_vehicle_weight_kg, d.dock_status "
         "FROM appointment_slots sl JOIN docks d ON d.dock_id = sl.dock_id WHERE sl.slot_id=?")
    return [conn.execute(q, (s,)).fetchone() for s in slot_ids]


def slot_claim_conflict(conn, slot_id: str, shipment_id: str, now: datetime) -> str | None:
    """Is someone else (or our own current appointment) already on this slot?

    Returns a reason string, or None if the slot is free to claim.
    Live holds = HELD and not expired, or CONFIRMED. Expiry is compared in Python.
    """
    appt = conn.execute(
        f"SELECT appointment_id, shipment_id FROM appointments WHERE slot_id=? "
        f"AND appointment_status IN {ACTIVE_APPT}", (slot_id,)).fetchone()
    if appt:
        return f"occupied by {appt['appointment_id']}"
    for h in conn.execute("SELECT * FROM slot_holds WHERE slot_id=? AND status IN ('HELD','CONFIRMED')",
                          (slot_id,)).fetchall():
        live = h["status"] == "CONFIRMED" or parse_ts(h["expires_at"]) > now
        if live and h["shipment_id"] != shipment_id:
            return f"held by another truck ({h['status']})"
        if live and h["shipment_id"] == shipment_id and h["status"] == "CONFIRMED":
            return "already booked for this shipment"
    return None


def check_hard_constraints(conn, ship, slot_ids: list[str], eta: EtaView, facts: DriverFacts | None,
                           now: datetime) -> list[str]:
    """Return a list of violations (empty list = feasible). Occupancy is checked separately."""
    hc = rules()["hard_constraints"]
    slots = _slots_with_docks(conn, slot_ids)
    if any(s is None for s in slots):
        return ["unknown slot"]
    v: list[str] = []
    first, last = slots[0], slots[-1]
    start, span_end = parse_ts(first["slot_start_ts"]), parse_ts(last["slot_end_ts"])
    unload_end = start + timedelta(minutes=ship["expected_unload_min"])
    dock = first["dock_code"]

    # Two slots must be the same dock and back-to-back.
    if len(slots) > hc["max_slots_per_option"]:
        v.append("too many slots")
    for a, b in zip(slots, slots[1:]):
        if a["dock_id"] != b["dock_id"] or a["slot_end_ts"] != b["slot_start_ts"]:
            v.append("slots not back-to-back on one dock")
    if first["facility_id"] != ship["destination_facility_id"]:
        v.append("wrong facility")
    for s in slots:
        if s["slot_status"] != "OPEN":
            v.append(f"{s['dock_code']} {hhmm(s['slot_start_ts'])} slot is {s['slot_status']}"
                     + (f" ({s['block_reason']})" if s["block_reason"] else ""))
        if s["dock_status"] != "ACTIVE":
            v.append(f"dock {s['dock_code']} is {s['dock_status']}")

    # Dock events (audit F2: the snapshot's view ignores these; we don't).
    if hc["dock_serviceable"]:
        for ev in conn.execute("SELECT * FROM dock_status_events WHERE dock_id=?", (first["dock_id"],)):
            ev_start = parse_ts(ev["event_start_ts"])
            ev_end = parse_ts(ev["event_end_ts"]) if ev["event_end_ts"] else datetime.max.replace(tzinfo=start.tzinfo)
            if start < ev_end and span_end > ev_start:
                label = (f"{dock} {ev['event_type']} {hhmm(ev_start)}-"
                         f"{hhmm(ev_end) if ev['event_end_ts'] else 'open-ended'} ({ev['dock_event_id']})")
                if ev["event_type"] in hc["unserviceable_event_types"]:
                    v.append(label)
                elif ev["event_type"] == "CAPACITY_REDUCTION" and hc["capacity_reduction_blocks"]:
                    v.append(label)

    if hc["dock_type_match"] and first["dock_type"] != required_dock_type(ship):
        v.append(f"needs {required_dock_type(ship)} dock, {dock} is {first['dock_type']}")
    if hc["weight_within_dock_limit"] and ship["load_weight_kg"] > first["max_vehicle_weight_kg"]:
        v.append(f"{ship['load_weight_kg']} kg > {dock} limit {first['max_vehicle_weight_kg']} kg")
    if hc["start_not_before_eta"] and start < parse_ts(eta.earliest_start_ts):
        v.append(f"starts before truck can be ready ({hhmm(eta.earliest_start_ts)})")
    if hc["start_after_now"] and start <= now:
        v.append("slot already started")
    if hc["unload_fits_contiguous_time"] and unload_end > span_end:
        v.append(f"{ship['expected_unload_min']} min unload does not fit")
    if start > at_local(start, hc["last_new_start"]):
        v.append(f"start after {hc['last_new_start']} needs manual approval (RULE005)")
    if unload_end > at_local(start, hc["facility_close"]):
        v.append(f"unload would end after facility close {hc['facility_close']}")

    # Driver's own constraints from the chat.
    if facts:
        if facts.earliest_wanted_ts and start < parse_ts(facts.earliest_wanted_ts):
            v.append(f"before driver's requested {hhmm(facts.earliest_wanted_ts)}")
        if facts.latest_finish_ts:
            buffer = hc["gate_out_buffer_min"] if facts.latest_finish_kind != "UNLOAD_END" else 0
            if unload_end + timedelta(minutes=buffer) > parse_ts(facts.latest_finish_ts):
                v.append(f"would not let driver leave by {hhmm(facts.latest_finish_ts)}")
    return v


def candidate_slot_groups(conn, ship) -> list[list[str]]:
    """All single slots plus back-to-back pairs on docks of the right type."""
    rows = conn.execute(
        "SELECT sl.slot_id, sl.dock_id, sl.slot_start_ts, sl.slot_end_ts FROM appointment_slots sl "
        "JOIN docks d ON d.dock_id = sl.dock_id WHERE sl.facility_id=? AND d.dock_type=?",
        (ship["destination_facility_id"], required_dock_type(ship))).fetchall()
    by_dock: dict[str, list] = {}
    for r in sorted(rows, key=lambda r: (r["dock_id"], parse_ts(r["slot_start_ts"]))):
        by_dock.setdefault(r["dock_id"], []).append(r)
    groups: list[list[str]] = []
    unload = timedelta(minutes=ship["expected_unload_min"])
    for slots in by_dock.values():
        for i, s in enumerate(slots):
            fits_one = parse_ts(s["slot_start_ts"]) + unload <= parse_ts(s["slot_end_ts"])
            if fits_one:
                groups.append([s["slot_id"]])
            elif i + 1 < len(slots) and slots[i + 1]["slot_start_ts"] == s["slot_end_ts"]:
                groups.append([s["slot_id"], slots[i + 1]["slot_id"]])  # e.g. a 75-min unload
    return groups


def _score(ship, eta: EtaView, start: datetime, n_slots: int, flags: list[str]) -> float:
    so = rules()["soft_objectives"]
    waiting = max(0.0, (start - parse_ts(eta.ts)).total_seconds() / 60)
    score = 100 - waiting * so["waiting_minutes_weight"]
    score -= so["two_slot_penalty"] * (n_slots - 1)
    score -= so["flagged_dock_penalty"] * len(flags)
    return round(score, 1)


def _flags(conn, slot_ids: list[str]) -> list[str]:
    """Soft warnings (allowed but shown), e.g. a capacity reduction on that dock."""
    slots = _slots_with_docks(conn, slot_ids)
    out = []
    start, end = parse_ts(slots[0]["slot_start_ts"]), parse_ts(slots[-1]["slot_end_ts"])
    for ev in conn.execute("SELECT * FROM dock_status_events WHERE dock_id=? AND event_type='CAPACITY_REDUCTION'",
                           (slots[0]["dock_id"],)):
        if start < parse_ts(ev["event_end_ts"] or "9999-12-31T00:00:00+05:30") and end > parse_ts(ev["event_start_ts"]):
            out.append(f"capacity reduction on {slots[0]['dock_code']} ({ev['dock_event_id']})")
    return out


def get_feasible_options(conn, shipment_id: str, eta: EtaView, facts: DriverFacts | None, now: datetime,
                         limit: int | None = 3, ignore_claims: bool = False) -> OptionsResult:
    ship = get_shipment(conn, shipment_id)
    feasible: list[Option] = []
    reasons: dict[str, int] = {}
    groups = candidate_slot_groups(conn, ship)
    for slot_ids in groups:
        violations = check_hard_constraints(conn, ship, slot_ids, eta, facts, now)
        if not violations and not ignore_claims:
            for sid in slot_ids:
                c = slot_claim_conflict(conn, sid, shipment_id, now)
                if c:
                    violations.append(f"slot taken ({c})")
        if violations:
            for r in violations:
                reasons[r] = reasons.get(r, 0) + 1
            continue
        slots = _slots_with_docks(conn, slot_ids)
        start = parse_ts(slots[0]["slot_start_ts"])
        flags = _flags(conn, slot_ids)
        why = [f"{slots[0]['dock_type']} dock {slots[0]['dock_code']} fits {ship['load_weight_kg']} kg",
               f"starts after truck is ready ({hhmm(eta.earliest_start_ts)})",
               f"{ship['expected_unload_min']} min unload fits" + (" across 2 back-to-back slots" if len(slot_ids) > 1 else "")]
        feasible.append(Option(
            option_id=option_id_for(slot_ids), slot_ids=slot_ids, dock_code=slots[0]["dock_code"],
            start_ts=slots[0]["slot_start_ts"], end_ts=slots[-1]["slot_end_ts"],
            unload_end_ts=iso(start + timedelta(minutes=ship["expected_unload_min"])),
            score=_score(ship, eta, start, len(slot_ids), flags), why_feasible=why, flags=flags))
    # Rank: best score first; tie-break earliest start, then dock code (stable + explainable).
    feasible.sort(key=lambda o: (-o.score, parse_ts(o.start_ts), o.dock_code))
    summary = _summarise_reasons(reasons, ship) if not feasible else []
    return OptionsResult(options=feasible[:limit] if limit else feasible, reasons_if_empty=summary,
                         candidates_checked=len(groups))


def _summarise_reasons(reasons: dict[str, int], ship) -> list[str]:
    """Group the 'why nothing fits' reasons so a human can read them in 5 seconds.
    Most actionable first: dock events, then blocked slots / RULE005, then capacity taken, then the rest."""
    def rank(text: str) -> int:
        if "(DEVT" in text:
            return 0
        if "BLOCKED" in text or "RULE005" in text or "close" in text:
            return 1
        if "taken" in text or "occupied" in text:
            return 2
        return 3
    out = [f"only {required_dock_type(ship)} docks considered for this load"]
    for text, n in sorted(reasons.items(), key=lambda kv: (rank(kv[0]), -kv[1]))[:6]:
        out.append(f"{text} [{n} option(s)]")
    return out


def truck_priority_score(ship, eta_confidence: str, in_yard: bool = False, already_told: bool = False) -> float:
    """Who deserves scarce capacity? Transparent formula from the YAML (decision 7)."""
    so = rules()["soft_objectives"]
    late = max(0.0, (parse_ts(ship["latest_eta_ts"] or ship["original_eta_ts"])
                     - parse_ts(ship["original_eta_ts"])).total_seconds() / 60)
    score = so["priority_weight"][ship["priority_code"]] + late * so["minutes_late_weight"]
    score += so["already_in_yard_bonus"] if in_yard else 0
    score -= so["schedule_change_penalty"] if already_told else 0
    score -= so["eta_confidence_penalty"].get(eta_confidence, 0)
    return round(score, 1)
