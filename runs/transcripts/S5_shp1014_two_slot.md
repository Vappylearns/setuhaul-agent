## S5_shp1014_two_slot: SHP1014 (CRITICAL, 21 t, 75 min) needs two back-to-back slots; D2 noon already went to SHP1005

_Shows: two-slot hold, several requests on one schedule_

**09:44 DRV014:** Origin released me late. ETA 11:25. This load is urgent. Can I get the first possible dock?

    tool find_shipment({}) -> ok
    tool record_eta({"shipment_id": "SHP1014", "arrival_ts": "2026-08-04T11:25:00+05:30", "confidence": "HIGH", "reason_code": "LO) -> ok
    tool get_feasible_slots({"shipment_id": "SHP1014"}) -> ok

**Agent:** For SHP1014, planning on you being ready from 11:25 (driver chat, HIGH confidence). These docks fit your load:  
  1) Dock D4 12:00-14:00 (2 back-to-back slots for your unload)  
  2) Dock D4 13:00-15:00 (2 back-to-back slots for your unload)  
  3) Dock D2 14:00-16:00 (2 back-to-back slots for your unload)  
Reply 1, 2 or 3 to hold one for 5 minutes. Nothing is booked yet.  
`outcome=OPTIONS`

**09:45 DRV014:** first one

    tool place_hold({"shipment_id": "SHP1014", "option_id": "OPT-68f8bb5a"}) -> ok

**Agent:** Holding option 1: Dock D4 12:00-14:00 until 09:50. Reply YES to confirm, or NO to release it.  
`outcome=HELD`

**09:45 DRV014:** yes confirm

    tool confirm_slot({"hold_group_id": "HG-62d82a2d80"}) -> ok

**Agent:** Confirmed: Dock D4 12:00-14:00 at Jaipur DC (appointment APT-DC-3a085d8f). Your old slot is released. You can check in at the gate from 11:00.  
`outcome=CONFIRMED`

**Result: PASS**
