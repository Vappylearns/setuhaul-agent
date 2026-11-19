from setuhaul.clock import parse_ts
from setuhaul.eta import effective_eta
from setuhaul.extractor import extract
from setuhaul.models import DriverFacts

T = "2026-08-04T{}:00+05:30"


def test_shp1017_low_range_plans_on_late_end(conn):
    e = effective_eta(conn, "SHP1017", parse_ts(T.format("10:02")))
    assert (e.source, e.confidence, e.earliest_start_ts[11:16]) == ("DRIVER_DECLARED", "LOW", "13:00")


def test_relative_delay_needs_clarification(conn):
    facts = extract("I am late by one hour.", parse_ts(T.format("09:19")))
    e = effective_eta(conn, "SHP1013", parse_ts(T.format("09:19")), facts)
    assert e.needs_clarification and e.confidence == "LOW"


def test_gate_checkin_means_start_now(conn):
    now = parse_ts(T.format("09:30"))
    e = effective_eta(conn, "SHP1005", now)
    assert e.source == "GATE_CHECKIN" and parse_ts(e.earliest_start_ts) == now


def test_changed_eta_not_labelled_original_plan(conn):
    # audit F6: v_latest_eta calls SHP1009's changed ETA 'ORIGINAL_PLAN'
    e = effective_eta(conn, "SHP1009", parse_ts(T.format("09:30")))
    assert e.source != "ORIGINAL_PLAN" and e.ts[11:16] == "10:50"


def test_future_eta_rows_are_ignored(conn):
    # ETA009 (11:25) was created at 09:45; at 09:44 we must not know it yet.
    e = effective_eta(conn, "SHP1014", parse_ts(T.format("09:44")))
    assert e.ts[11:16] == "10:15"


def test_ops_override_beats_driver(conn):
    facts = DriverFacts(arrival_ts=T.format("15:00"))
    e = effective_eta(conn, "SHP_T0", parse_ts(T.format("10:00")), facts)
    assert e.source == "OPERATIONS_OVERRIDE"
