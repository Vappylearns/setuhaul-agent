"""Guardrails: deterministic checks around the LLM. They don't trust the model.

  mask_pii          - phones, emails, licence/registration numbers never reach logs or the LLM
  injection_screen  - flags "ignore your rules" / free-text "book SLOT-..." attempts
  is_off_topic      - refuse things that are not about the driver's delivery
  output_check      - the reply may say "confirmed" ONLY if confirm_slot succeeded this turn
Session isolation (driver can only touch their own shipment) is enforced in tools.py.
"""
from __future__ import annotations

import re

_PII = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<EMAIL>"),
    (re.compile(r"\+91[-\s]?\d{2,5}[-\s]?\d{4,10}"), "<PHONE>"),
    (re.compile(r"(?<!\d)\d{10}(?!\d)"), "<PHONE>"),
    # Indian licence / vehicle registration, e.g. RJ14DL1001, RJ14 AB 1234, HR26GT4102
    (re.compile(r"\b[A-Z]{2}[-\s]?\d{1,2}[-\s]?[A-Z]{1,3}[-\s]?\d{3,4}\b"), "<VEHICLE_OR_LICENCE>"),
]


def mask_pii(text: str) -> str:
    for pattern, token in _PII:
        text = pattern.sub(token, text)
    return text


def mask_obj(obj):
    """Recursively mask every string in a dict/list (used before writing traces)."""
    if isinstance(obj, str):
        return mask_pii(obj)
    if isinstance(obj, dict):
        return {k: mask_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask_obj(v) for v in obj]
    return obj


_INJECTION = [
    r"ignore (all |your |the |previous |prior )*(rules|instructions)",
    r"you are now", r"system prompt", r"developer mode", r"pretend (to be|you)",
    r"\bbook (slot )?SLOT-", r"\bSLOT-[A-Z]{3}-\d{3}\b", r"override", r"jailbreak",
]


def injection_screen(text: str) -> list[str]:
    return [p for p in _INJECTION if re.search(p, text, re.I)]


_DOMAIN = re.compile(
    r"slot|dock|late|reach|eta|arriv|traffic|unload|gate|truck|trailer|load|shipment|ord-|shp|appoint|book|"
    r"confirm|delay|breakdown|broken|tyre|tire|reefer|leave|pickup|yard|minute|hour|option|cancel|status|"
    r"warehouse|waiting|wait|stuck|toll|road|time|today|tonight|pm\b|am\b|\d|"
    r"accident|injur|hurt|fire|emergency|unsafe|ambulance|crash|smoke|robbery|unwell", re.I)
_OFF_TOPIC = re.compile(
    r"\bjoke|cricket|movie|song|poem|recipe|weather forecast|politic|stock price|bitcoin|write (me )?(a |some )?code|"
    r"capital of|who (is|won)|girlfriend|horoscope", re.I)


def is_off_topic(text: str) -> bool:
    if _OFF_TOPIC.search(text):
        return True
    return len(text.split()) > 4 and not _DOMAIN.search(text)


_CLAIM_CONFIRMED = re.compile(r"\b(confirmed|booked|reserved|guaranteed)\b", re.I)
_CLAIM_HELD = re.compile(r"\b(i('ve| have)? (held|am holding|'m holding)|holding (it|option|this|that|the slot)|is (now )?held)\b", re.I)
_NEGATED = re.compile(r"\b(not|no|isn't|is not|nothing|never|until|before)\b[^.?!]{0,25}\b(confirmed|booked|reserved|guaranteed)\b", re.I)


def output_check(reply: str, turn_events: list[dict]) -> tuple[str, str | None]:
    """Block over-promising. Returns (safe_reply, block_reason or None)."""
    confirmed = any(e["tool"] == "confirm_slot" and e.get("ok") for e in turn_events)
    held = confirmed or any(e["tool"] == "place_hold" and e.get("ok") for e in turn_events)
    stripped = _NEGATED.sub("", reply)
    if _CLAIM_CONFIRMED.search(stripped) and not confirmed:
        return ("I have not booked anything yet. Nothing is final until the scheduling system "
                "commits it. Please pick one of the options I listed, or tell me what you need."), \
            "claimed confirmed without confirm_slot"
    if _CLAIM_HELD.search(reply) and not held:
        return ("I have not placed a hold yet. Please pick one of the options I listed."), \
            "claimed hold without place_hold"
    return reply, None
