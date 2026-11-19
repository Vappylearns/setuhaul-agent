## S4_heavy_and_rule005: DRV007 drives two trucks: agent asks which; heavy trailer gets D6 only; 21:30 request -> human (RULE005)

_Shows: clarify which shipment, dock type rules, escalation trigger_

**09:39 DRV007:** Heavy trailer ETA 12:00. Original heavy-bay slot will be missed. What is next?

    tool find_shipment({}) -> ok

**Agent:** You have 2 shipments today. Is this about ORD-260804-007 (SHP1007) or ORD-260804-016 (SHP1016)?  
`outcome=QUESTION`

**09:39 DRV007:** SHP1016

    tool find_shipment({"shipment_ref": "SHP1016"}) -> ok
    tool record_eta({"shipment_id": "SHP1016", "arrival_ts": "2026-08-04T12:00:00+05:30", "confidence": "HIGH"}) -> ok
    tool get_feasible_slots({"shipment_id": "SHP1016"}) -> ok

**Agent:** For SHP1016, planning on you being ready from 12:00 (driver chat, HIGH confidence). These docks fit your load:  
  1) Dock D6 12:30-14:00  
  2) Dock D6 14:00-15:30  
  3) Dock D6 15:30-17:00  
Reply 1, 2 or 3 to hold one for 5 minutes. Nothing is booked yet.  
`outcome=OPTIONS`

**09:40 DRV007:** Can you book 21:30 instead?

    tool escalate({"shipment_id": "SHP1016", "reason_code": "AFTER_LAST_NEW_START", "summary_for_human": "driver asked for 21:30) -> ok

**Agent:** Unloading can't start after 21:00 without a manager's approval (RULE005), so I can't book that myself. I've sent case ESC-FDC7C8FE to the warehouse coordinator to decide; they will contact you.  
`outcome=ESCALATED`

**Result: PASS**
