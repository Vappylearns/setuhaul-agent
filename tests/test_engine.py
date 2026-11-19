from setuhaul import engine
from setuhaul.clock import parse_ts
from setuhaul.eta import effective_eta
from setuhaul.models import DriverFacts

T = "2026-08-04T{}:00+05:30"


def opts(conn, sid, now, facts=None, limit=3):
    n = parse_ts(T.format(now))
    e = effective_eta(conn, sid, n, facts)
    return engine.get_feasible_options(conn, sid, e, facts, n, limit=limit)


def test_shp1017_35_candidates_from_1300(conn):
    r = opts(conn, "SHP1017", "10:02", limit=None)
    assert len(r.options) == 35
    starts = sorted(o.start_ts[11:16] for o in r.options)
    assert starts[0] == "13:00" and starts[-1] == "21:00"
    assert {o.dock_code for o in r.options[:4]} == {"D1", "D2", "D3", "D4"}


def test_shp1014_only_two_slot_options_on_d2_d4(conn):
    r = opts(conn, "SHP1014", "09:44", DriverFacts(arrival_ts=T.format("11:25")), limit=None)
    assert r.options and all(len(o.slot_ids) == 2 for o in r.options)
    assert {o.dock_code for o in r.options} <= {"D2", "D4"}   # D1/D3 are 20,000 kg docks
    assert [o.slot_ids for o in r.options[:2]] == [["SLOT-JAI-019", "SLOT-JAI-020"], ["SLOT-JAI-047", "SLOT-JAI-048"]]


def test_shp1015_reefer_empty_cites_maintenance(conn):
    r = opts(conn, "SHP1015", "16:24", DriverFacts(arrival_ts=T.format("18:30")))
    assert r.options == []
    assert any("DEVT002" in x for x in r.reasons_if_empty)


def test_shp1016_heavy_d6_only_first_1230(conn):
    r = opts(conn, "SHP1016", "09:39", DriverFacts(arrival_ts=T.format("12:00")))
    assert {o.dock_code for o in r.options} == {"D6"} and r.options[0].slot_ids == ["SLOT-JAI-074"]


def test_shp1010_reefer_d5_only(conn):
    r = opts(conn, "SHP1010", "09:39", limit=None)
    assert r.options and {o.dock_code for o in r.options} == {"D5"}


def test_shp1012_gate_out_1330_only_noon_slots(conn):
    f = DriverFacts(arrival_ts=T.format("11:10"), latest_finish_ts=T.format("13:30"), latest_finish_kind="GATE_OUT")
    r = opts(conn, "SHP1012", "09:29", f, limit=None)
    assert sorted(o.slot_ids[0] for o in r.options) == ["SLOT-JAI-019", "SLOT-JAI-047"]


def test_d3_never_offered_during_breakdown(conn):
    r = opts(conn, "SHP1007", "08:00", limit=None)
    for o in r.options:
        if o.dock_code == "D3":
            assert o.start_ts[11:16] >= "13:00"


def test_rule005_and_timezone_trap(conn):
    """21:00 start is allowed; 21:30 is not. Done in Python: SQLite's time() would say 16:00 (UTC)."""
    assert conn.execute("SELECT time('2026-08-04T21:30:00+05:30')").fetchone()[0] == "16:00:00"
    ship = engine.get_shipment(conn, "SHP1017")
    e = effective_eta(conn, "SHP1017", parse_ts(T.format("10:02")))
    assert engine.check_hard_constraints(conn, ship, ["SLOT-JAI-014"], e, None, parse_ts(T.format("10:02"))) == []
    assert not any(o.start_ts[11:16] > "21:00" for o in opts(conn, "SHP1017", "10:02", limit=None).options)


def test_noon_squeeze_priority_scores(conn):
    s = {sid: engine.get_shipment(conn, sid) for sid in ("SHP1014", "SHP1006", "SHP1012", "SHP1013")}
    assert engine.truck_priority_score(s["SHP1014"], "HIGH") == 135
    assert engine.truck_priority_score(s["SHP1006"], "HIGH") == 90
    assert engine.truck_priority_score(s["SHP1012"], "HIGH") == 72.5
    assert engine.truck_priority_score(s["SHP1013"], "LOW") == 50
