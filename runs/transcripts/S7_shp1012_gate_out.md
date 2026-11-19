## S7_shp1012_gate_out: SHP1012 must leave by 13:30: agent asks gate-out vs unload-end; SHP1017's reschedule freed D1 12:00

_Shows: clarify, driver constraint, schedule changes after another booking_

**10:05 DRV012:** Tyre repaired. I can reach 11:10. I need to leave the warehouse before 1:30 for another pickup.

    tool find_shipment({}) -> ok

**Agent:** Should 13:30 be the latest time you leave the gate (gate-out), or the latest time unloading must finish?  
`outcome=QUESTION`

**10:06 DRV012:** gate-out

    tool record_eta({"shipment_id": "SHP1012", "arrival_ts": "2026-08-04T11:10:00+05:30", "confidence": "HIGH", "reason_code": "BR) -> ok
    tool get_feasible_slots({"shipment_id": "SHP1012", "latest_finish_ts": "2026-08-04T13:30:00+05:30", "latest_finish_kind": "GATE_OUT"}) -> ok

**Agent:** For SHP1012, planning on you being ready from 11:10 (driver chat, HIGH confidence). These docks fit your load:  
  1) Dock D1 12:00-13:00  
Reply 1 to hold it for 5 minutes. Nothing is booked yet.  
`outcome=OPTIONS`

**Result: PASS**
