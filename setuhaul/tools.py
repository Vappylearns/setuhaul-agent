"""The 7 allowlisted tools the agent may call. This is the ONLY way to touch capacity.

Security rules enforced here (not in the prompt):
  * driver_id / session_id are injected from the session - the model can never choose them.
  * every shipment_id is checked against the logged-in driver (NOT_YOUR_SHIPMENT otherwise).
  * place_hold only accepts an option_id the engine offered THIS conversation
    (no free-text booking: "book SLOT-JAI-019" is impossible by construction).
  * idempotency keys are made by code: sha256(session | tool | args | turn_id).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import booking, engine, escalation
from .clock import hhmm, parse_ts
from .eta import effective_eta, record_eta
from .guardrails import mask_pii
from .models import DriverFacts
from .tracing import Timer, Tracer

ACTIVE_STATUSES = ("PLANNED", "ASSIGNED", "IN_TRANSIT", "AT_GATE", "WAITING", "IN_DOCK")


@dataclass
class ToolContext:
    conn: object
    driver_id: str
    session_id: str
    state: dict            # conversation state (persisted in conversations.state_json)
    now: datetime
    turn_id: str
    tracer: Tracer
    events: list = field(default_factory=list)   # what happened this turn (for the output guardrail)


# ---------- input schemas (exported to the LLM as JSON schema) ----------

class FindShipmentIn(BaseModel):
    shipment_ref: Optional[str] = Field(None, description="shipment_id (SHP1017) or order reference (ORD-260804-017)")


class RecordEtaIn(BaseModel):
    shipment_id: str
    arrival_ts: Optional[str] = Field(None, description="ISO time with +05:30, e.g. 2026-08-04T12:45:00+05:30")
    range_start: Optional[str] = None
    range_end: Optional[str] = None
    confidence: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    reason_code: Optional[str] = Field(None, description="TRAFFIC|BREAKDOWN|WEATHER|LATE_DEPARTURE|LOADING_DELAY|ROUTE_ISSUE|OTHER")
    is_repair_time_only: bool = False
    raw_text: str = ""


class GetFeasibleSlotsIn(BaseModel):
    shipment_id: str
    earliest_ts: Optional[str] = Field(None, description="driver wants nothing before this ISO time")
    latest_finish_ts: Optional[str] = None
    latest_finish_kind: Optional[Literal["GATE_OUT", "UNLOAD_END"]] = None
    limit: int = 3


class PlaceHoldIn(BaseModel):
    shipment_id: str
    option_id: str = Field(description="must be an option_id returned by get_feasible_slots in this chat")


class ConfirmSlotIn(BaseModel):
    hold_group_id: str


class ReleaseHoldIn(BaseModel):
    hold_group_id: str
    reason: str = "driver declined"


class EscalateIn(BaseModel):
    shipment_id: Optional[str] = None
    reason_code: Literal["NO_FEASIBLE_OPTION", "AFTER_LAST_NEW_START", "SAFETY", "CONTRADICTORY_DATA",
                         "BUMP_REQUEST", "DRIVER_REQUEST"]
    summary_for_human: str = ""


# ---------- helpers ----------

def _key(ctx: ToolContext, tool: str, args: dict) -> str:
    canon = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(f"{ctx.session_id}|{tool}|{canon}|{ctx.turn_id}".encode()).hexdigest()


def _scope(ctx: ToolContext, shipment_id: str) -> dict | None:
    """Session isolation: returns an error dict if this shipment isn't the driver's."""
    ship = engine.get_shipment(ctx.conn, shipment_id)
    if ship is None:
        return {"ok": False, "error": "UNKNOWN_SHIPMENT"}
    if ship["driver_id"] != ctx.driver_id:
        ctx.tracer.log("guardrail_block", ctx.turn_id, rule="session_scope",
                       detail=f"{ctx.driver_id} tried to access {shipment_id}")
        ctx.state.setdefault("guardrail_events", []).append("NOT_YOUR_SHIPMENT")
        return {"ok": False, "error": "NOT_YOUR_SHIPMENT"}
    return None


def _facts(ctx: ToolContext) -> DriverFacts:
    return DriverFacts.model_validate(ctx.state.get("facts") or {})


def _option_view(o) -> dict:
    return {"option_id": o.option_id, "dock": o.dock_code, "start": hhmm(o.start_ts), "end": hhmm(o.end_ts),
            "unload_until": hhmm(o.unload_end_ts), "slots": o.slot_ids, "score": o.score, "flags": o.flags}


def _offer(ctx: ToolContext, shipment_id: str, facts: DriverFacts, limit: int = 3) -> dict:
    eta = effective_eta(ctx.conn, shipment_id, ctx.now, facts)
    res = engine.get_feasible_options(ctx.conn, shipment_id, eta, facts, ctx.now, limit=limit)
    ctx.state["last_options"] = [o.model_dump() for o in res.options]   # OFFERED lives only here
    ctx.state["last_eta"] = eta.model_dump()
    ctx.state["last_reasons"] = res.reasons_if_empty
    return {"ok": True, "effective_eta": {"ts": hhmm(eta.ts), "plan_from": hhmm(eta.earliest_start_ts),
                                          "source": eta.source, "confidence": eta.confidence},
            "options": [_option_view(o) for o in res.options], "reasons_if_empty": res.reasons_if_empty,
            "candidates_checked": res.candidates_checked}


# ---------- the tools ----------

def find_shipment(ctx: ToolContext, shipment_ref: str | None = None) -> dict:
    rows = ctx.conn.execute(
        f"SELECT * FROM shipments WHERE driver_id=? AND current_status IN {ACTIVE_STATUSES}", (ctx.driver_id,)).fetchall()
    if shipment_ref:
        ref = shipment_ref.strip().upper()
        other = ctx.conn.execute("SELECT * FROM shipments WHERE shipment_id=? OR order_reference=?", (ref, ref)).fetchone()
        if other is not None:
            err = _scope(ctx, other["shipment_id"])
            if err:
                return err
        rows = [r for r in rows if ref in (r["shipment_id"], r["order_reference"])]
    out = []
    for s in rows:
        a = ctx.conn.execute(
            "SELECT a.appointment_id, a.appointment_status, d.dock_code, sl.slot_start_ts, sl.slot_end_ts "
            "FROM appointments a JOIN appointment_slots sl USING(slot_id) JOIN docks d ON d.dock_id=sl.dock_id "
            "WHERE a.shipment_id=? AND a.is_current=1", (s["shipment_id"],)).fetchone()
        eta = effective_eta(ctx.conn, s["shipment_id"], ctx.now, None)
        out.append({"shipment_id": s["shipment_id"], "order_reference": s["order_reference"],
                    "status": s["current_status"], "priority": s["priority_code"],
                    "dock_needed": engine.required_dock_type(s), "weight_kg": s["load_weight_kg"],
                    "unload_min": s["expected_unload_min"],
                    "current_appointment": None if not a else {
                        "appointment_id": a["appointment_id"], "status": a["appointment_status"],
                        "dock": a["dock_code"], "window": f"{hhmm(a['slot_start_ts'])}-{hhmm(a['slot_end_ts'])}"},
                    "effective_eta": {"ts": hhmm(eta.ts), "source": eta.source, "confidence": eta.confidence}})
    if len(out) == 1:
        ctx.state["shipment_id"] = out[0]["shipment_id"]
    return {"ok": True, "shipments": out, "count": len(out)}


def record_eta_tool(ctx: ToolContext, **kw) -> dict:
    a = RecordEtaIn(**kw)
    if err := _scope(ctx, a.shipment_id):
        return err
    facts = _facts(ctx)
    if a.arrival_ts:
        facts.arrival_ts = a.arrival_ts
    if a.range_start and a.range_end:
        facts.arrival_range_start, facts.arrival_range_end = a.range_start, a.range_end
    facts.confidence, facts.is_repair_time_only = a.confidence, a.is_repair_time_only
    ctx.state["facts"] = facts.model_dump()
    eta = effective_eta(ctx.conn, a.shipment_id, ctx.now, facts)
    if eta.needs_clarification:
        return {"ok": False, "error": "NEEDS_CLARIFICATION", "reason": eta.reason}
    eid = record_eta(ctx.conn, shipment_id=a.shipment_id, driver_id=ctx.driver_id, eta=eta,
                     reason_code=a.reason_code, masked_text=mask_pii(a.raw_text), now=ctx.now)
    return {"ok": True, "eta_update_id": eid, "effective_eta": {"ts": hhmm(eta.ts), "plan_from": hhmm(eta.earliest_start_ts),
                                                                "source": eta.source, "confidence": eta.confidence}}


def get_feasible_slots(ctx: ToolContext, **kw) -> dict:
    a = GetFeasibleSlotsIn(**kw)
    if err := _scope(ctx, a.shipment_id):
        return err
    facts = _facts(ctx)
    if a.earliest_ts:
        facts.earliest_wanted_ts = a.earliest_ts
    if a.latest_finish_ts:
        facts.latest_finish_ts, facts.latest_finish_kind = a.latest_finish_ts, a.latest_finish_kind or "GATE_OUT"
    ctx.state["facts"] = facts.model_dump()
    return _offer(ctx, a.shipment_id, facts, limit=min(max(a.limit, 1), 5))


def place_hold(ctx: ToolContext, **kw) -> dict:
    a = PlaceHoldIn(**kw)
    if err := _scope(ctx, a.shipment_id):
        return err
    offered = {o["option_id"]: o for o in ctx.state.get("last_options", [])}
    if a.option_id not in offered:
        ctx.tracer.log("guardrail_block", ctx.turn_id, rule="option_id_only", detail=a.option_id)
        return {"ok": False, "error": "UNKNOWN_OPTION", "reason": "only options offered in this chat can be held"}
    opt = offered[a.option_id]
    res = booking.place_hold(ctx.conn, shipment_id=a.shipment_id, slot_ids=opt["slot_ids"], session_id=ctx.session_id,
                             idempotency_key=_key(ctx, "place_hold", kw), now=ctx.now, facts=_facts(ctx))
    if res.ok:
        ctx.state["open_hold"] = {"hold_group_id": res.hold_group_id, "option_id": a.option_id,
                                  "expires_at": res.expires_at, "dock": opt["dock_code"],
                                  "start": hhmm(opt["start_ts"]), "end": hhmm(opt["end_ts"]),
                                  "slot_ids": opt["slot_ids"]}
        return {"ok": True, "hold_group_id": res.hold_group_id, "expires_at": hhmm(res.expires_at),
                "dock": opt["dock_code"], "start": hhmm(opt["start_ts"]), "end": hhmm(opt["end_ts"]),
                "replayed": res.replayed}
    # TAKEN / NOT_FEASIBLE: re-run the engine so the agent can offer the next option immediately.
    nxt = _offer(ctx, a.shipment_id, _facts(ctx))
    return {"ok": False, "error": res.error, "reason": res.reason, "next_options": nxt["options"],
            "reasons_if_empty": nxt["reasons_if_empty"]}


def confirm_slot(ctx: ToolContext, **kw) -> dict:
    a = ConfirmSlotIn(**kw)
    res = booking.confirm_slot(ctx.conn, hold_group_id=a.hold_group_id, session_id=ctx.session_id,
                               idempotency_key=_key(ctx, "confirm_slot", kw), now=ctx.now)
    hold = ctx.state.get("open_hold") or {}
    if res.ok:
        ctx.state["open_hold"] = None
        ctx.state["confirmed"] = {**hold, "appointment_id": res.appointment_id}
        return {"ok": True, "status": "CONFIRMED", "appointment_id": res.appointment_id, "dock": hold.get("dock"),
                "start": hold.get("start"), "end": hold.get("end"), "replaced": res.reason}
    if res.error == "HOLD_EXPIRED":
        ctx.state["open_hold"] = None
    return {"ok": False, "error": res.error, "reason": res.reason}


def release_hold(ctx: ToolContext, **kw) -> dict:
    a = ReleaseHoldIn(**kw)
    res = booking.release_hold(ctx.conn, hold_group_id=a.hold_group_id, session_id=ctx.session_id, reason=a.reason,
                               idempotency_key=_key(ctx, "release_hold", kw), now=ctx.now)
    if res.ok:
        ctx.state["open_hold"] = None
    return {"ok": res.ok, "released": res.ok, "error": res.error}


def escalate(ctx: ToolContext, **kw) -> dict:
    a = EscalateIn(**kw)
    if a.shipment_id and (err := _scope(ctx, a.shipment_id)):
        return err
    facts = _facts(ctx)
    # The hand-off is built from TOOL FACTS (state), not from the model's prose.
    record = escalation.escalate(
        ctx.conn, driver_id=ctx.driver_id, shipment_id=a.shipment_id, reason_code=a.reason_code, now=ctx.now,
        effective_eta=ctx.state.get("last_eta"),
        driver_constraints={k: v for k, v in facts.model_dump().items()
                            if k in ("arrival_ts", "earliest_wanted_ts", "latest_finish_ts", "latest_finish_kind") and v},
        options_checked=[{"option": _option_view_dict(o), "result": "offered"} for o in ctx.state.get("last_options", [])],
        why_nothing_fits=ctx.state.get("last_reasons") or [a.summary_for_human],
        what_driver_was_told=ctx.state.get("pending_driver_text") or
        "No automatic slot is possible; a person from operations will contact you. Don't go to a dock until then.",
        summary_for_human=mask_pii(a.summary_for_human))
    ctx.state["escalated_case"] = record.case_id
    owner = next((c for c in record.contact_chain if c["status"] == "OK"), None)
    return {"ok": True, "case_id": record.case_id, "owner_role": owner["hop"] if owner else None,
            "delivery_status": "QUEUED" if owner else "FAILED",
            "skipped_hops": [c for c in record.contact_chain if c["status"] != "OK"]}


def _option_view_dict(o: dict) -> dict:
    return {"dock": o["dock_code"], "start": hhmm(o["start_ts"]), "slots": o["slot_ids"]}


# ---------- registry + dispatcher ----------

REGISTRY = {
    "find_shipment": (find_shipment, FindShipmentIn, "List the logged-in driver's active shipments (or one by ref)."),
    "record_eta": (record_eta_tool, RecordEtaIn, "Save the driver's new arrival time / range."),
    "get_feasible_slots": (get_feasible_slots, GetFeasibleSlotsIn,
                           "Ranked feasible dock options (code-checked). Only these may be offered."),
    "place_hold": (place_hold, PlaceHoldIn, "Hold an offered option for 5 minutes."),
    "confirm_slot": (confirm_slot, ConfirmSlotIn, "Confirm a held option. Only after the driver says yes."),
    "release_hold": (release_hold, ReleaseHoldIn, "Release a hold the driver no longer wants."),
    "escalate": (escalate, EscalateIn, "Hand off to a human with a complete record."),
}


def openai_tool_schemas() -> list[dict]:
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": m.model_json_schema()}}
            for n, (_, m, d) in REGISTRY.items()]


def call_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    """The single entry point: allowlist, validate, run, trace."""
    if name not in REGISTRY:
        ctx.tracer.log("guardrail_block", ctx.turn_id, rule="tool_allowlist", detail=name)
        return {"ok": False, "error": "UNKNOWN_TOOL"}
    fn, model, _ = REGISTRY[name]
    with Timer() as t:
        try:
            clean = model(**(args or {})).model_dump(exclude_none=True)
            result = fn(ctx, **clean)
        except Exception as e:  # validation errors become a tool error the agent can recover from
            result = {"ok": False, "error": "BAD_ARGS", "reason": str(e)[:300]}
    ctx.events.append({"tool": name, "args": args, "ok": bool(result.get("ok")), "result": result})
    ctx.tracer.log("tool_call", ctx.turn_id, tool=name, args=args, ok=result.get("ok"),
                   error=result.get("error"), ms=t.ms, result=result)
    return result
