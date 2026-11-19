## S1_clarify: SHP1013 says only 'late by one hour' -> agent asks one question first

_Shows: clarify_

**09:19 DRV013:** I am late by one hour.

    tool find_shipment({}) -> ok

**Agent:** Does that mean you will now reach around 11:00, or could the delay grow? Please tell me the time you expect to arrive.  
`outcome=QUESTION`

**09:20 DRV013:** I'll reach 11:00

    tool record_eta({"shipment_id": "SHP1013", "arrival_ts": "2026-08-04T11:00:00+05:30", "confidence": "HIGH"}) -> ok
    tool get_feasible_slots({"shipment_id": "SHP1013"}) -> ok

**Agent:** For SHP1013, planning on you being ready from 11:00 (driver chat, HIGH confidence). These docks fit your load:  
  1) Dock D2 12:00-13:00  
  2) Dock D4 12:00-13:00  
  3) Dock D1 13:00-14:00  
Reply 1, 2 or 3 to hold one for 5 minutes. Nothing is booked yet.  
`outcome=OPTIONS`

**Result: PASS**
