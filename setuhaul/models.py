"""Pydantic models: the typed contract between the LLM, the tools and the database.

If the model produces something that doesn't validate, we retry or ask a plain
question - we never let a malformed value reach the booking service.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Intent = Literal[
    "REPORT_DELAY", "ASK_SLOT_OPTIONS", "CHECK_STATUS", "EARLY_ARRIVAL",
    "CHOOSE_OPTION", "CONFIRM", "DECLINE", "OFF_TOPIC", "OTHER",
]


class DriverFacts(BaseModel):
    """What we extracted from one driver message (LLM or rule-based extractor)."""

    intent: Intent = "OTHER"
    arrival_ts: Optional[str] = None            # absolute ISO time, if the driver gave one
    arrival_range_start: Optional[str] = None
    arrival_range_end: Optional[str] = None
    relative_delay_min: Optional[int] = None    # "late by one hour" -> 60 (ambiguous on its own)
    is_repair_time_only: bool = False           # "repair takes 1 hour" is NOT an arrival time
    latest_finish_ts: Optional[str] = None
    latest_finish_kind: Literal["GATE_OUT", "UNLOAD_END", "UNKNOWN"] | None = None
    earliest_wanted_ts: Optional[str] = None    # "anything after 12"
    chosen_option_index: Optional[int] = None   # 0-based
    shipment_ref: Optional[str] = None          # SHP#### or ORD-######-###
    safety_flag: bool = False
    confidence: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    delay_reason_code: Optional[str] = None
    missing: list[str] = Field(default_factory=list)


class EtaView(BaseModel):
    ts: str
    earliest_start_ts: str
    source: str
    confidence: str
    range_start: Optional[str] = None
    range_end: Optional[str] = None
    needs_clarification: bool = False
    reason: str = ""


class Option(BaseModel):
    option_id: str
    slot_ids: list[str]
    dock_code: str
    start_ts: str
    end_ts: str          # end of the claimed slot span
    unload_end_ts: str   # when unloading should finish
    score: float
    why_feasible: list[str]
    flags: list[str] = Field(default_factory=list)


class OptionsResult(BaseModel):
    options: list[Option]
    reasons_if_empty: list[str] = Field(default_factory=list)
    candidates_checked: int = 0


class BookingResult(BaseModel):
    ok: bool
    error: Optional[str] = None      # TAKEN | NOT_FEASIBLE | HOLD_EXPIRED | NOT_YOUR_HOLD | UNKNOWN_HOLD
    reason: str = ""
    hold_group_id: Optional[str] = None
    expires_at: Optional[str] = None
    appointment_id: Optional[str] = None
    slot_ids: list[str] = Field(default_factory=list)
    replayed: bool = False           # True when served from idempotency_log


class HandoffRecord(BaseModel):
    """Everything a human needs to act without re-asking the driver (brief p9)."""

    case_id: str
    shipment_id: Optional[str]
    order_reference: Optional[str]
    priority_code: Optional[str]
    driver_id: str
    driver_phone_masked: Optional[str]
    reason_code: str
    current_appointment: Optional[dict]
    effective_eta: Optional[dict]
    driver_constraints: dict
    options_checked: list[dict]
    why_nothing_fits: list[str]
    what_driver_was_told: str
    recommended_action: str
    contact_chain: list[dict]


class AgentReply(BaseModel):
    text: str
    session_id: str
    turn_id: str
    tool_calls: list[dict] = Field(default_factory=list)
    outcome: Optional[str] = None      # QUESTION | OPTIONS | HELD | CONFIRMED | ESCALATED | REFUSED | DEDUPED | INFO
    dedupe_hit: bool = False
    guardrail_events: list[str] = Field(default_factory=list)
