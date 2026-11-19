"""ETA confidence layer: decides WHICH arrival time we plan on, and how sure we are.

This is code, not the LLM, so the answer is repeatable and explainable.
Precedence (first match wins):
  1 gate check-in (truck is physically here)   4 latest DRIVER_DECLARED row
  2 OPERATIONS_OVERRIDE row                     5 WAREHOUSE_ESTIMATE row
  3 fresh driver statement in this chat         6 shipments.latest_eta_ts / original_eta_ts
Rows created after `now` are ignored (no peeking into the future of the snapshot).
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timedelta

from .clock import iso, parse_ts
from .models import DriverFacts, EtaView

_RANGE_IN_NOTE = re.compile(r"between (\d{1,2}:\d{2}) and (\d{1,2}:\d{2})")


def _rows(conn, shipment_id: str, source: str, now: datetime) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM eta_updates WHERE shipment_id=? AND source_type=?", (shipment_id, source)
    ).fetchall()
    rows = [r for r in rows if parse_ts(r["created_at"]) <= now]
    return sorted(rows, key=lambda r: parse_ts(r["created_at"]))


def _view(ts: datetime, now: datetime, source: str, confidence: str, reason: str,
          range_start: datetime | None = None, range_end: datetime | None = None,
          needs_clarification: bool = False) -> EtaView:
    # LOW confidence with a range => plan on the LATE end (don't promise what may not happen)
    plan = range_end if (range_end and confidence == "LOW") else ts
    return EtaView(
        ts=iso(ts), earliest_start_ts=iso(max(plan, now)), source=source, confidence=confidence,
        range_start=iso(range_start) if range_start else None,
        range_end=iso(range_end) if range_end else None,
        needs_clarification=needs_clarification, reason=reason,
    )


def effective_eta(conn, shipment_id: str, now: datetime, facts: DriverFacts | None = None) -> EtaView:
    ship = conn.execute("SELECT * FROM shipments WHERE shipment_id=?", (shipment_id,)).fetchone()
    if ship is None:
        raise ValueError(f"unknown shipment {shipment_id}")

    # 1. Gate check-in is a fact: the truck is here, it can start now.
    chk = conn.execute("SELECT gate_in_ts FROM facility_checkins WHERE shipment_id=?", (shipment_id,)).fetchone()
    if chk and chk["gate_in_ts"] and parse_ts(chk["gate_in_ts"]) <= now:
        gate_in = parse_ts(chk["gate_in_ts"])
        return EtaView(ts=iso(gate_in), earliest_start_ts=iso(now), source="GATE_CHECKIN",
                       confidence="HIGH", reason=f"truck checked in at gate {gate_in:%H:%M}")

    # 2. Operations override wins over anything the driver says.
    ops = _rows(conn, shipment_id, "OPERATIONS_OVERRIDE", now)
    if ops:
        r = ops[-1]
        return _view(parse_ts(r["declared_eta_ts"]), now, "OPERATIONS_OVERRIDE", "HIGH", "ops override")

    # 3. What the driver told us in THIS conversation.
    if facts:
        if facts.arrival_range_start and facts.arrival_range_end:
            rs, re_ = parse_ts(facts.arrival_range_start), parse_ts(facts.arrival_range_end)
            return _view(rs, now, "DRIVER_CHAT", "LOW", "driver gave a range; planning on the late end",
                         range_start=rs, range_end=re_)
        if facts.arrival_ts:
            return _view(parse_ts(facts.arrival_ts), now, "DRIVER_CHAT", facts.confidence, "driver stated arrival")
        if facts.relative_delay_min is not None or facts.is_repair_time_only:
            # "late by one hour" / "repair 1 hour" is NOT an arrival time -> ask, don't guess.
            base = parse_ts(ship["latest_eta_ts"] or ship["original_eta_ts"])
            guess = base + timedelta(minutes=facts.relative_delay_min or 0)
            return _view(guess, now, "DRIVER_CHAT", "LOW", "relative delay only; arrival time unclear",
                         needs_clarification=True)

    # 4. Latest driver-declared row from the database.
    drv = _rows(conn, shipment_id, "DRIVER_DECLARED", now)
    if drv:
        r = drv[-1]
        ts = parse_ts(r["declared_eta_ts"])
        m = _RANGE_IN_NOTE.search(r["note"] or "")
        if r["confidence_code"] == "LOW" and m:
            day = ts
            rs = day.replace(hour=int(m[1].split(":")[0]), minute=int(m[1].split(":")[1]))
            re_ = day.replace(hour=int(m[2].split(":")[0]), minute=int(m[2].split(":")[1]))
            return _view(ts, now, "DRIVER_DECLARED", "LOW", "declared range; planning on the late end",
                         range_start=rs, range_end=re_)
        return _view(ts, now, "DRIVER_DECLARED", r["confidence_code"], r["note"] or "driver declared")

    # 5. Warehouse estimate.
    wh = _rows(conn, shipment_id, "WAREHOUSE_ESTIMATE", now)
    if wh:
        return _view(parse_ts(wh[-1]["declared_eta_ts"]), now, "WAREHOUSE_ESTIMATE", "MEDIUM", "warehouse estimate")

    # 6. Shipment record. We compute our OWN label (fixes audit F6: the view calls a
    #    changed ETA 'ORIGINAL_PLAN'). Ignore latest_eta_ts if it came from a future row.
    future = conn.execute(
        "SELECT declared_eta_ts, created_at FROM eta_updates WHERE shipment_id=?", (shipment_id,)
    ).fetchall()
    latest = ship["latest_eta_ts"]
    if latest and any(parse_ts(f["created_at"]) > now and f["declared_eta_ts"] == latest for f in future):
        latest = None
    if latest and latest != ship["original_eta_ts"]:
        ts, source = parse_ts(latest), "SHIPMENT_LATEST_ETA"
    else:
        ts, source = parse_ts(ship["original_eta_ts"]), "ORIGINAL_PLAN"
    confidence = "HIGH" if now <= ts else "LOW"  # past the ETA with no gate-in => doubtful
    return _view(ts, now, source, confidence, "from shipment record")


def record_eta(conn, *, shipment_id: str, driver_id: str, eta: EtaView, reason_code: str | None,
               masked_text: str, now: datetime) -> str:
    """Persist a driver-declared ETA (note = MASKED raw text, never raw PII)."""
    allowed = {"TRAFFIC", "BREAKDOWN", "WEATHER", "LATE_DEPARTURE", "LOADING_DELAY", "ROUTE_ISSUE", "OTHER"}
    eta_id = "ETA-DC-" + uuid.uuid4().hex[:8]
    planned = eta.range_end if (eta.confidence == "LOW" and eta.range_end) else eta.ts
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO eta_updates (eta_update_id, shipment_id, source_type, reported_by_driver_id, "
            "declared_eta_ts, confidence_code, delay_reason_code, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (eta_id, shipment_id, "DRIVER_DECLARED", driver_id, planned, eta.confidence,
             reason_code if reason_code in allowed else "OTHER", masked_text[:300], iso(now)),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return eta_id
