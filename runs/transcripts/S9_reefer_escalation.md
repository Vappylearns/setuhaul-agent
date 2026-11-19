## S9_reefer_escalation: SHP1015 reefer, ETA 18:30, but D5 is in maintenance 18:00-22:00 -> honest escalation

_Shows: no feasible slot, escalation hand-off, contact fallback (audit F5)_

**16:24 DRV015:** Evening traffic. ETA 6:30. Can the reefer unload tonight?

    tool find_shipment({}) -> ok
    tool record_eta({"shipment_id": "SHP1015", "arrival_ts": "2026-08-04T18:30:00+05:30", "confidence": "HIGH", "reason_code": "TR) -> ok
    tool get_feasible_slots({"shipment_id": "SHP1015"}) -> ok
    tool escalate({"shipment_id": "SHP1015", "reason_code": "NO_FEASIBLE_OPTION", "summary_for_human": "only REEFER docks consid) -> ok

**Agent:** I can't find a safe slot for you today (D5 MAINTENANCE 18:00-22:00 (DEVT002)). I've passed case ESC-8CCF5B46 to the warehouse coordinator with all your details, so you won't need to repeat anything. They will contact you; please don't go to a dock until they do.  
`outcome=ESCALATED`

**Result: PASS**
