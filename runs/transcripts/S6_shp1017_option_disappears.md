## S6_shp1017_option_disappears: SHP1017 asks for slots after 1 PM; another truck grabs its first choice before it answers

_Shows: compare options, two compete for capacity, option disappears, reschedule_

**10:02 DRV001:** What slots are possible after 1 PM if I reach around 12:45?

    tool find_shipment({}) -> ok
    tool record_eta({"shipment_id": "SHP1017", "arrival_ts": "2026-08-04T12:45:00+05:30", "confidence": "MEDIUM"}) -> ok
    tool get_feasible_slots({"shipment_id": "SHP1017", "earliest_ts": "2026-08-04T13:00:00+05:30"}) -> ok

**Agent:** For SHP1017, planning on you being ready from 12:45 (driver chat, MEDIUM confidence). These docks fit your load:  
  1) Dock D1 13:00-14:00  
  2) Dock D3 13:00-14:00  
  3) Dock D1 14:00-15:00  
Reply 1, 2 or 3 to hold one for 5 minutes. Nothing is booked yet.  
`outcome=OPTIONS`

> *10:02* **[another driver, SHP1018]** holds Dock D1 13:00 -> ok

**10:03 DRV001:** first one

    tool place_hold({"shipment_id": "SHP1017", "option_id": "OPT-f710d67a"}) -> TAKEN

**Agent:** Sorry, Dock D1 13:00 was just taken by another truck, so I couldn't hold it. Next options:  
  1) Dock D3 13:00-14:00  
  2) Dock D1 14:00-15:00  
  3) Dock D2 14:00-15:00  
Reply 1, 2 or 3.  
`outcome=OPTIONS`

**10:03 DRV001:** 1

    tool place_hold({"shipment_id": "SHP1017", "option_id": "OPT-f62887d8"}) -> ok

**Agent:** Holding option 1: Dock D3 13:00-14:00 until 10:08. Reply YES to confirm, or NO to release it.  
`outcome=HELD`

**10:04 DRV001:** yes

    tool confirm_slot({"hold_group_id": "HG-e8605cfd67"}) -> ok

**Agent:** Confirmed: Dock D3 13:00-14:00 at Jaipur DC (appointment APT-DC-30649e6f). Your old slot is released. You can check in at the gate from 12:00.  
`outcome=CONFIRMED`

**Result: PASS**
