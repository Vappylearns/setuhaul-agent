"""Integrity proof (deliverable 5): 10 drivers race for ONE slot. Exactly one wins."""
import sqlite3
import threading
import time

import pytest

from setuhaul import booking, db
from setuhaul.clock import parse_ts

FIXED_NOW = parse_ts("2026-08-04T10:00:00+05:30")


@pytest.mark.parametrize("run", range(20))  # 20 repeated races, 0 double bookings allowed
def test_ten_drivers_one_slot(working_db, run):
    barrier = threading.Barrier(10)
    results, lock = [], threading.Lock()

    def attempt(i):
        conn = db.connect(working_db)  # one connection per thread, like separate API workers
        barrier.wait()                 # fire together
        r = booking.place_hold(conn, shipment_id=f"SHP_T{i}", slot_ids=["SLOT-JAI-020"], session_id=f"S{i}",
                               idempotency_key=f"k{i}", now=FIXED_NOW)
        if r.ok:
            r = booking.confirm_slot(conn, hold_group_id=r.hold_group_id, session_id=f"S{i}",
                                     idempotency_key=f"c{i}", now=FIXED_NOW)
        with lock:
            results.append(r)
        conn.close()

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(r.ok for r in results) == 1
    assert all(r.error == "TAKEN" for r in results if not r.ok)
    conn = db.connect(working_db)
    assert booking.overlapping_active_claims(conn) == 0
    assert conn.execute("SELECT count(*) FROM appointments WHERE slot_id='SLOT-JAI-020' "
                        "AND appointment_status='CONFIRMED'").fetchone()[0] == 1


def test_same_key_retried_10x(working_db):
    out = []
    barrier = threading.Barrier(10)

    def attempt():
        conn = db.connect(working_db)
        barrier.wait()
        out.append(booking.place_hold(conn, shipment_id="SHP_T1", slot_ids=["SLOT-JAI-020"], session_id="S1",
                                      idempotency_key="same-key", now=FIXED_NOW))
    ts = [threading.Thread(target=attempt) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len({r.hold_group_id for r in out}) == 1 and all(r.ok for r in out)
    conn = db.connect(working_db)
    assert conn.execute("SELECT count(*) FROM slot_holds").fetchone()[0] == 1


def test_two_slot_vs_one_slot_race(working_db):
    from setuhaul.models import DriverFacts
    now = parse_ts("2026-08-04T09:44:00+05:30")
    barrier, out = threading.Barrier(2), {}

    def big():
        conn = db.connect(working_db)
        barrier.wait()
        out["big"] = booking.place_hold(conn, shipment_id="SHP1014", slot_ids=["SLOT-JAI-019", "SLOT-JAI-020"],
                                        session_id="S14", idempotency_key="b", now=now,
                                        facts=DriverFacts(arrival_ts="2026-08-04T11:25:00+05:30"))

    def small():
        conn = db.connect(working_db)
        barrier.wait()
        out["small"] = booking.place_hold(conn, shipment_id="SHP_T3", slot_ids=["SLOT-JAI-020"], session_id="S3",
                                          idempotency_key="s", now=now)
    ts = [threading.Thread(target=big), threading.Thread(target=small)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out["big"].ok != out["small"].ok   # exactly one wins
    assert booking.overlapping_active_claims(db.connect(working_db), now) == 0


@pytest.mark.failure_demo
def test_naive_baseline_double_books(working_db):
    """Staged failure #1: read-then-write with no lock and no unique index. Proves WHY we need BEGIN IMMEDIATE."""
    setup = sqlite3.connect(working_db)
    setup.execute("CREATE TABLE naive_claims (slot_id TEXT, shipment_id TEXT)")  # no unique index on purpose
    setup.commit()
    setup.close()
    barrier, winners, lock = threading.Barrier(10), [], threading.Lock()

    def naive(i):
        c = sqlite3.connect(working_db, timeout=30)
        barrier.wait()
        free = c.execute("SELECT count(*) FROM naive_claims WHERE slot_id='SLOT-JAI-020'").fetchone()[0] == 0
        time.sleep(0.05)  # the gap between 'looks free' and 'write' is where double booking happens
        if free:
            c.execute("INSERT INTO naive_claims VALUES ('SLOT-JAI-020', ?)", (f"SHP_T{i}",))
            c.commit()
            with lock:
                winners.append(i)
        c.close()
    ts = [threading.Thread(target=naive, args=(i,)) for i in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(winners) > 1   # the naive design DOES double-book
