"""Escalation: a complete hand-off record for a human, written to a SIMULATED outbox.

It never sends a real email. Routing walks a contact chain and only counts a hop
as usable if a real active contact with an email exists (fixes audit F5, where
OM004 went to night.shift@... which no contact owns and FAILED silently).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from . import config
from .clock import hhmm, iso, local_time
from .guardrails import mask_pii
from .models import HandoffRecord

# The legacy evening alias seen in operational_messages (OM004). No contact owns it.
LEGACY_EVENING_ALIAS = "night.shift@setuhaul.example"
ROLE_CHAIN = ["WAREHOUSE_COORDINATOR", "SHIFT_MANAGER"]

RECOMMENDED = {
    "NO_FEASIBLE_OPTION": "Review capacity manually: consider another facility, a planner override, or next-day slot.",
    "AFTER_LAST_NEW_START": "Decide whether to approve a start after 21:00 (RULE005).",
    "SAFETY": "Call the driver now. Safety first; do not discuss slots until the driver is safe.",
    "CONTRADICTORY_DATA": "Check the gate log and call the driver to resolve the contradiction.",
    "BUMP_REQUEST": "Decide whether to move an already-confirmed truck. The agent never bumps anyone.",
    "DRIVER_REQUEST": "Driver asked for a person. Call back.",
}


def _contact_chain(conn, facility_id: str, now: datetime) -> list[dict]:
    chain: list[dict] = []
    if local_time(now).hour >= 16:  # evening routing used the legacy alias (F5)
        match = conn.execute("SELECT contact_id FROM facility_contacts WHERE email=? AND active_flag=1",
                             (LEGACY_EVENING_ALIAS,)).fetchone()
        chain.append({"hop": "legacy evening alias", "contact_id": match["contact_id"] if match else None,
                      "address": LEGACY_EVENING_ALIAS,
                      "status": "OK" if match else "FAILED: no active contact owns this address"})
    for role in ROLE_CHAIN:
        row = conn.execute("SELECT contact_id, contact_role, email FROM facility_contacts WHERE facility_id=? "
                           "AND contact_role=? AND active_flag=1", (facility_id, role)).fetchone()
        if row and row["email"]:
            chain.append({"hop": role, "contact_id": row["contact_id"], "address": mask_pii(row["email"]),
                          "status": "OK"})
        else:
            chain.append({"hop": role, "contact_id": None, "address": None, "status": "FAILED: no email"})
    return chain


def escalate(conn, *, driver_id: str, shipment_id: str | None, reason_code: str, now: datetime,
             effective_eta: dict | None, driver_constraints: dict, options_checked: list[dict],
             why_nothing_fits: list[str], what_driver_was_told: str, summary_for_human: str = "") -> HandoffRecord:
    ship = conn.execute("SELECT * FROM shipments WHERE shipment_id=?", (shipment_id,)).fetchone() if shipment_id else None
    drv = conn.execute("SELECT phone FROM drivers WHERE driver_id=?", (driver_id,)).fetchone()
    appt = None
    if ship:
        a = conn.execute(
            "SELECT a.appointment_id, a.appointment_status, sl.slot_id, d.dock_code, sl.slot_start_ts, sl.slot_end_ts "
            "FROM appointments a JOIN appointment_slots sl USING(slot_id) JOIN docks d ON d.dock_id=sl.dock_id "
            "WHERE a.shipment_id=? AND a.is_current=1", (shipment_id,)).fetchone()
        if a:
            appt = {"appointment_id": a["appointment_id"], "status": a["appointment_status"], "slot_id": a["slot_id"],
                    "dock": a["dock_code"], "window": f"{hhmm(a['slot_start_ts'])}-{hhmm(a['slot_end_ts'])}"}
    facility = ship["destination_facility_id"] if ship else "FAC-JAI-01"
    chain = _contact_chain(conn, facility, now)
    owner = next((c for c in chain if c["status"] == "OK"), None)
    record = HandoffRecord(
        case_id="ESC-" + uuid.uuid4().hex[:8].upper(), shipment_id=shipment_id,
        order_reference=ship["order_reference"] if ship else None,
        priority_code=ship["priority_code"] if ship else None, driver_id=driver_id,
        driver_phone_masked=mask_pii(drv["phone"]) if drv else None, reason_code=reason_code,
        current_appointment=appt, effective_eta=effective_eta, driver_constraints=driver_constraints,
        options_checked=options_checked,
        why_nothing_fits=why_nothing_fits or [summary_for_human or reason_code],
        what_driver_was_told=what_driver_was_told,
        recommended_action=RECOMMENDED.get(reason_code, "Review and call the driver."), contact_chain=chain)
    status = "QUEUED" if owner else "FAILED"  # QUEUED = written to the simulated outbox
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO escalations VALUES (?,?,?,?,?,?,?,?)",
                     (record.case_id, shipment_id, driver_id, reason_code, record.model_dump_json(),
                      owner["contact_id"] if owner else None, status, iso(now)))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    config.OUTBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    with config.OUTBOX_FILE.open("a") as f:  # SIMULATED outbox - nothing is actually sent
        f.write(json.dumps({"to_contact_id": owner["contact_id"] if owner else None, "status": status,
                            "handoff": record.model_dump()}, default=str) + "\n")
    return record
