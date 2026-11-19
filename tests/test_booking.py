from datetime import timedelta

from setuhaul import booking
from setuhaul.clock import parse_ts

NOW = parse_ts("2026-08-04T10:00:00+05:30")


def hold(conn, sid="SHP_T1", slots=("SLOT-JAI-020",), key="k1", session="S1", now=NOW):
    return booking.place_hold(conn, shipment_id=sid, slot_ids=list(slots), session_id=session,
                              idempotency_key=key, now=now)


def test_hold_then_confirm(conn):
    h = hold(conn)
    assert h.ok
    c = booking.confirm_slot(conn, hold_group_id=h.hold_group_id, session_id="S1", idempotency_key="c1", now=NOW)
    assert c.ok and c.appointment_id
    row = conn.execute("SELECT * FROM appointments WHERE appointment_id=?", (c.appointment_id,)).fetchone()
    assert (row["appointment_status"], row["booking_source"], row["slot_id"]) == ("CONFIRMED", "DRIVER_CHAT", "SLOT-JAI-020")


def test_same_key_twice_same_response_one_row(conn):
    a, b = hold(conn), hold(conn)
    assert a.hold_group_id == b.hold_group_id and b.replayed
    assert conn.execute("SELECT count(*) FROM slot_holds").fetchone()[0] == 1


def test_hold_expires_and_slot_is_claimable_again(conn):
    assert hold(conn).ok
    later = NOW + timedelta(seconds=301)
    assert hold(conn, sid="SHP_T2", key="k2", session="S2", now=later).ok


def test_confirm_expired_hold(conn):
    h = hold(conn)
    c = booking.confirm_slot(conn, hold_group_id=h.hold_group_id, session_id="S1", idempotency_key="c1",
                             now=NOW + timedelta(seconds=301))
    assert not c.ok and c.error == "HOLD_EXPIRED"


def test_occupied_slot_is_taken(conn):
    r = hold(conn, sid="SHP_T1", slots=("SLOT-JAI-005",))  # APT1017 sits here
    assert not r.ok and r.error == "TAKEN"


def test_two_slot_hold_blocks_both(conn):
    from setuhaul.models import DriverFacts
    f = DriverFacts(arrival_ts="2026-08-04T11:25:00+05:30")
    r = booking.place_hold(conn, shipment_id="SHP1014", slot_ids=["SLOT-JAI-019", "SLOT-JAI-020"], session_id="S14",
                           idempotency_key="k14", now=parse_ts("2026-08-04T09:44:00+05:30"), facts=f)
    assert r.ok
    assert not hold(conn, slots=("SLOT-JAI-020",), key="kx", session="SX",
                    now=parse_ts("2026-08-04T09:45:00+05:30")).ok   # 2nd slot is blocked too


def test_reschedule_cancels_old_and_links(conn):
    h = booking.place_hold(conn, shipment_id="SHP1017", slot_ids=["SLOT-JAI-006"], session_id="S17",
                           idempotency_key="h17", now=NOW)
    assert h.ok, h.reason
    c = booking.confirm_slot(conn, hold_group_id=h.hold_group_id, session_id="S17", idempotency_key="c17", now=NOW)
    old = conn.execute("SELECT * FROM appointments WHERE appointment_id='APT1017'").fetchone()
    new = conn.execute("SELECT * FROM appointments WHERE appointment_id=?", (c.appointment_id,)).fetchone()
    assert (old["is_current"], old["appointment_status"]) == (0, "CANCELLED")
    assert new["replaced_appointment_id"] == "APT1017" and new["is_current"] == 1


def test_wrong_session_cannot_confirm(conn):
    h = hold(conn)
    c = booking.confirm_slot(conn, hold_group_id=h.hold_group_id, session_id="INTRUDER", idempotency_key="c9", now=NOW)
    assert not c.ok and c.error == "NOT_YOUR_HOLD"


def test_release(conn):
    h = hold(conn)
    assert booking.release_hold(conn, hold_group_id=h.hold_group_id, session_id="S1", reason="x",
                                idempotency_key="r1", now=NOW).ok
    assert hold(conn, sid="SHP_T2", key="k2", session="S2").ok
