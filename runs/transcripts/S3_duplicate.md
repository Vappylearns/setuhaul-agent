## S3_duplicate: DRV006 sends the same text twice on two threads, 62 s apart (MSG001/MSG015), plus a retried API call

_Shows: idempotency, dedupe_

**09:34 DRV006:** Traffic after Shahpura. Reaching around 11:20. Any slot after 12?

    tool find_shipment({}) -> ok

**Agent:** You have 2 shipments today. Is this about ORD-260804-006 (SHP1006) or ORD-260804-021 (SHP1021)?  
`outcome=QUESTION`

**09:35 DRV006:** Traffic after Shahpura. Reaching around 11:20. Any slot after 12?

    dedupe_hit: replayed earlier reply, nothing new written

**Agent:** You have 2 shipments today. Is this about ORD-260804-006 (SHP1006) or ORD-260804-021 (SHP1021)?  
`outcome=DEDUPED`

**09:35 DRV006:** Traffic after Shahpura. Reaching around 11:20. Any slot after 12? *(retry of same turn id)*

    tool find_shipment({}) -> ok

    dedupe_hit: replayed earlier reply, nothing new written

**Agent:** You have 2 shipments today. Is this about ORD-260804-006 (SHP1006) or ORD-260804-021 (SHP1021)?  
`outcome=QUESTION`

**09:36 DRV006:** SHP1006

    tool find_shipment({"shipment_ref": "SHP1006"}) -> ok
    tool record_eta({"shipment_id": "SHP1006", "arrival_ts": "2026-08-04T11:20:00+05:30", "confidence": "MEDIUM", "reason_code": ") -> ok
    tool get_feasible_slots({"shipment_id": "SHP1006", "earliest_ts": "2026-08-04T12:00:00+05:30"}) -> ok

**Agent:** For SHP1006, planning on you being ready from 11:20 (driver chat, MEDIUM confidence). These docks fit your load:  
  1) Dock D4 12:00-13:00  
  2) Dock D1 13:00-14:00  
  3) Dock D3 13:00-14:00  
Reply 1, 2 or 3 to hold one for 5 minutes. Nothing is booked yet.  
`outcome=OPTIONS`

**Result: PASS**
