"""Booking service: the slot state machine. It OWNS the truth.

    AVAILABLE -> HELD (expires in 5 min) -> CONFIRMED
                                         -> RELEASED (driver declined)
                                         -> EXPIRED  (time ran out)

Every public function = ONE `BEGIN IMMEDIATE` transaction:
  1. idempotency first  (same key -> same stored answer, nothing new written)
  2. sweep expired holds
  3. re-check the slot INSIDE the lock (what the driver saw a minute ago may be gone)
  4. write, log the response under the idempotency key, COMMIT
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta

from . import config, engine
from .clock import iso, parse_ts
from .db import write_txn
from .eta import effective_eta
from .models import BookingResult, DriverFacts


# ---------- helpers ----------

def _replay(conn, key: str) -> BookingResult | None:
    row = conn.execute("SELECT response_json FROM idempotency_log WHERE idempotency_key=?", (key,)).fetchone()
    if row:
        res = BookingResult.model_validate_json(row["response_json"])
        res.replayed = True
        return res
    return None


def _log(conn, key: str, op: str, res: BookingResult, now: datetime) -> BookingResult:
    conn.execute("INSERT INTO idempotency_log VALUES (?,?,?,?)", (key, op, res.model_dump_json(), iso(now)))
    return res


def sweep_expired(conn, now: datetime) -> int:
    """HELD -> EXPIRED once expires_at has passed (compared in Python, not SQL)."""
    n = 0
    for h in conn.execute("SELECT hold_id, expires_at FROM slot_holds WHERE status='HELD'").fetchall():
        if parse_ts(h["expires_at"]) <= now:
            conn.execute("UPDATE slot_holds SET status='EXPIRED' WHERE hold_id=?", (h["hold_id"],))
            n += 1
    return n


def is_slot_claimable(conn, slot_id: str, shipment_id: str, now: datetime,
                      facts: DriverFacts | None = None, slot_group: list[str] | None = None) -> tuple[bool, str]:
    """Re-check one slot inside the lock: free AND still passes every hard constraint."""
    conflict = engine.slot_claim_conflict(conn, slot_id, shipment_id, now)
    if conflict:
        return False, "TAKEN: " + conflict
    ship = engine.get_shipment(conn, shipment_id)
    eta = effective_eta(conn, shipment_id, now, facts)
    violations = engine.check_hard_constraints(conn, ship, slot_group or [slot_id], eta, facts, now)
    if violations:
        return False, "NOT_FEASIBLE: " + "; ".join(violations)
    return True, "ok"


# ---------- public API ----------

def place_hold(conn, *, shipment_id: str, slot_ids: list[str], session_id: str, idempotency_key: str,
               now: datetime, facts: DriverFacts | None = None) -> BookingResult:
    try:
        with write_txn(conn):
            prior = _replay(conn, idempotency_key)
            if prior:
                return prior
            sweep_expired(conn, now)
            for sid in slot_ids:
                ok, why = is_slot_claimable(conn, sid, shipment_id, now, facts, slot_group=slot_ids)
                if not ok:
                    code = why.split(":")[0]
                    # Failures are logged too, so a retried call gets the same answer.
                    return _log(conn, idempotency_key, "place_hold",
                                BookingResult(ok=False, error=code, reason=why, slot_ids=slot_ids), now)
            # One live hold per shipment: release this shipment's older HELD groups first.
            conn.execute("UPDATE slot_holds SET status='RELEASED' WHERE shipment_id=? AND status='HELD'",
                         (shipment_id,))
            group = "HG-" + uuid.uuid4().hex[:10]
            expires = now + timedelta(seconds=config.HOLD_TTL_SECONDS)
            for sid in slot_ids:
                conn.execute(
                    "INSERT INTO slot_holds (hold_id, hold_group_id, slot_id, shipment_id, session_id, "
                    "idempotency_key, status, created_at, expires_at) VALUES (?,?,?,?,?,?,'HELD',?,?)",
                    ("H-" + uuid.uuid4().hex[:10], group, sid, shipment_id, session_id, idempotency_key,
                     iso(now), iso(expires)))
            return _log(conn, idempotency_key, "place_hold",
                        BookingResult(ok=True, hold_group_id=group, expires_at=iso(expires), slot_ids=slot_ids), now)
    except sqlite3.IntegrityError as e:
        # The partial unique index ux_live_claim_per_slot is the last line of defence.
        return BookingResult(ok=False, error="TAKEN", reason=f"database refused second claim: {e}", slot_ids=slot_ids)


def confirm_slot(conn, *, hold_group_id: str, session_id: str, idempotency_key: str,
                 now: datetime) -> BookingResult:
    with write_txn(conn):
        prior = _replay(conn, idempotency_key)
        if prior:
            return prior
        sweep_expired(conn, now)
        holds = conn.execute("SELECT * FROM slot_holds WHERE hold_group_id=? ORDER BY slot_id",
                             (hold_group_id,)).fetchall()

        def fail(code: str, why: str) -> BookingResult:
            return _log(conn, idempotency_key, "confirm_slot",
                        BookingResult(ok=False, error=code, reason=why, hold_group_id=hold_group_id), now)

        if not holds:
            return fail("UNKNOWN_HOLD", "no such hold")
        if any(h["session_id"] != session_id for h in holds):
            return fail("NOT_YOUR_HOLD", "hold belongs to another session")
        if all(h["status"] == "CONFIRMED" for h in holds):
            return BookingResult(ok=True, hold_group_id=hold_group_id, appointment_id=holds[0]["appointment_id"],
                                 slot_ids=[h["slot_id"] for h in holds], replayed=True)
        if any(h["status"] == "EXPIRED" for h in holds):
            return fail("HOLD_EXPIRED", "the 5-minute hold ran out")
        if any(h["status"] != "HELD" for h in holds):
            return fail("HOLD_RELEASED", "hold was released")

        shipment_id = holds[0]["shipment_id"]
        slot_ids = [h["slot_id"] for h in holds]
        ts = iso(now)
        # Old BEFORE new: the unique index on current appointments would reject the insert otherwise.
        old = conn.execute(
            f"SELECT appointment_id FROM appointments WHERE shipment_id=? AND is_current=1 "
            f"AND appointment_status IN {engine.ACTIVE_APPT}", (shipment_id,)).fetchone()
        old_id = old["appointment_id"] if old else None
        if old_id:
            conn.execute("UPDATE appointments SET is_current=0, appointment_status='CANCELLED', cancelled_at=?, "
                         "cancellation_reason='RESCHEDULED_BY_DRIVER_CHAT', updated_at=? WHERE appointment_id=?",
                         (ts, ts, old_id))
            # Its old CONFIRMED holds (e.g. the 2nd slot of a two-slot booking) are freed too.
            conn.execute("UPDATE slot_holds SET status='RELEASED' WHERE appointment_id=? AND status='CONFIRMED'",
                         (old_id,))
        appt_id = "APT-DC-" + uuid.uuid4().hex[:8]
        conn.execute(
            "INSERT INTO appointments (appointment_id, shipment_id, slot_id, appointment_status, booking_source, "
            "is_current, booked_at, confirmed_at, replaced_appointment_id, updated_at) "
            "VALUES (?,?,?,'CONFIRMED','DRIVER_CHAT',1,?,?,?,?)",
            (appt_id, shipment_id, slot_ids[0], ts, ts, old_id, ts))
        # The 2nd slot of a two-slot option stays blocked by its CONFIRMED hold.
        conn.execute("UPDATE slot_holds SET status='CONFIRMED', appointment_id=? WHERE hold_group_id=?",
                     (appt_id, hold_group_id))
        return _log(conn, idempotency_key, "confirm_slot",
                    BookingResult(ok=True, hold_group_id=hold_group_id, appointment_id=appt_id,
                                  slot_ids=slot_ids, reason=f"replaced {old_id}" if old_id else ""), now)


def release_hold(conn, *, hold_group_id: str, session_id: str, reason: str, idempotency_key: str,
                 now: datetime) -> BookingResult:
    with write_txn(conn):
        prior = _replay(conn, idempotency_key)
        if prior:
            return prior
        sweep_expired(conn, now)
        cur = conn.execute("UPDATE slot_holds SET status='RELEASED' WHERE hold_group_id=? AND session_id=? "
                           "AND status='HELD'", (hold_group_id, session_id))
        res = BookingResult(ok=cur.rowcount > 0, hold_group_id=hold_group_id, reason=reason,
                            error=None if cur.rowcount else "NOTHING_TO_RELEASE")
        return _log(conn, idempotency_key, "release_hold", res, now)


def overlapping_active_claims(conn, now: datetime | None = None) -> int:
    """Integrity invariant. MUST always be 0.

    Counts slots claimed by more than one shipment (active appointment or live hold),
    plus pairs of different-shipment claims on one dock whose time ranges overlap.
    """
    claims = []  # (shipment_id, dock_id, start, end)
    for r in conn.execute(
            f"SELECT a.shipment_id, sl.slot_id, sl.dock_id, sl.slot_start_ts, sl.slot_end_ts FROM appointments a "
            f"JOIN appointment_slots sl USING (slot_id) WHERE a.appointment_status IN {engine.ACTIVE_APPT}"):
        claims.append((r["shipment_id"], r["slot_id"], r["dock_id"], r["slot_start_ts"], r["slot_end_ts"]))
    for r in conn.execute(
            "SELECT h.shipment_id, h.status, h.expires_at, sl.slot_id, sl.dock_id, sl.slot_start_ts, sl.slot_end_ts "
            "FROM slot_holds h JOIN appointment_slots sl USING (slot_id) WHERE h.status IN ('HELD','CONFIRMED')"):
        if r["status"] == "HELD" and now and parse_ts(r["expires_at"]) <= now:
            continue
        claims.append((r["shipment_id"], r["slot_id"], r["dock_id"], r["slot_start_ts"], r["slot_end_ts"]))
    bad = 0
    for i, a in enumerate(claims):
        for b in claims[i + 1:]:
            if a[0] == b[0] or a[2] != b[2]:
                continue  # same shipment (its appointment + its own hold) or different dock
            if parse_ts(a[3]) < parse_ts(b[4]) and parse_ts(b[3]) < parse_ts(a[4]):
                bad += 1
    return bad


def hold_details(conn, hold_group_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM slot_holds WHERE hold_group_id=?", (hold_group_id,)).fetchall()


def response_json(res: BookingResult) -> str:
    return json.dumps(res.model_dump())
