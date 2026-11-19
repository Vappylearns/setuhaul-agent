## S2_d3_breakdown: SHP1005 is at the gate but dock D3 broke down -> reassigned to a dock that fits 20.5 t / 75 min

_Shows: dock event is a hard constraint, two-slot option, compare options_

**09:30 DRV005:** My dock D3 broke down and I am waiting at the gate. Where should I go now?

    tool find_shipment({}) -> ok
    tool get_feasible_slots({"shipment_id": "SHP1005"}) -> ok

**Agent:** For SHP1005, planning on you being ready from 09:30 (gate checkin, HIGH confidence). These docks fit your load:  
  1) Dock D2 12:00-14:00 (2 back-to-back slots for your unload)  
  2) Dock D4 12:00-14:00 (2 back-to-back slots for your unload)  
  3) Dock D2 13:00-15:00 (2 back-to-back slots for your unload)  
Reply 1, 2 or 3 to hold one for 5 minutes. Nothing is booked yet.  
`outcome=OPTIONS`

**09:31 DRV005:** 1

    tool place_hold({"shipment_id": "SHP1005", "option_id": "OPT-cdc4d7ab"}) -> ok

**Agent:** Holding option 1: Dock D2 12:00-14:00 until 09:36. Reply YES to confirm, or NO to release it.  
`outcome=HELD`

**09:31 DRV005:** yes

    tool confirm_slot({"hold_group_id": "HG-af9fa8a307"}) -> ok

**Agent:** Confirmed: Dock D2 12:00-14:00 at Jaipur DC (appointment APT-DC-87d8f43f). Your old slot is released. You can check in at the gate from 11:00.  
`outcome=CONFIRMED`

**Result: PASS**
