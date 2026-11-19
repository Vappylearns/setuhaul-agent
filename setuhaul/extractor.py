"""Offline (no-LLM) extractor: turns a driver's message into DriverFacts with rules.

This is the deterministic fallback so the demo runs with no API key. In live mode
the LLM does this extraction instead, and this module is the safety net if the LLM
returns something invalid. Either way the OUTPUT is the same validated DriverFacts.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .clock import IST, iso
from .models import DriverFacts

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "half an": 0.5, "half": 0.5, "an": 1, "a": 1}

_TIME = re.compile(
    r"(?<![\w-])(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?![\w-])|\b(noon|midday)\b", re.I)
_NOT_TIME_AFTER = re.compile(r"^\s*(min|mins|minute|minutes|hour|hours|hr|hrs|kg|km|ton|tonnes|st|nd|rd|th)\b", re.I)
_BARE_OK_BEFORE = re.compile(r"(after|before|by|eta|reach|reaching|around|at|till|until|from|between|and|to|leave)\s*$", re.I)
_NOT_TIME_BEFORE = re.compile(r"(option|number|no\.?|slot|dock|#)\s*$", re.I)
_MISSED_SLOT = re.compile(r"(make|miss|missed|missing|reach my|for my|for the)\s*(my\s*|the\s*)?$", re.I)
_REF = re.compile(r"\b(SHP_?T?\d{1,4}|ORD-\d{6}-\d{3})\b", re.I)
_SLOTREF = re.compile(r"\bSLOT-[A-Z]{3}-\d{3}\b", re.I)


def _resolve_hour(h: int, m: int, ampm: str | None, now: datetime) -> datetime:
    """'6:30' at 16:24 means 18:30; '11:00' at 09:19 means 11:00. Pick the sensible one."""
    base = now.astimezone(IST).replace(second=0, microsecond=0)
    if ampm:
        pm = ampm.lower().startswith("p")
        h = (h % 12) + (12 if pm else 0)
        return base.replace(hour=h, minute=m)
    candidates = [h] if h >= 12 else [h, h + 12]
    for hh in candidates:
        if hh <= 23:
            dt = base.replace(hour=hh, minute=m)
            if dt >= base - timedelta(minutes=60):
                return dt
    return base.replace(hour=min(candidates[-1], 23), minute=m)


def find_times(text: str, now: datetime) -> list[tuple[datetime, str]]:
    """Return [(datetime, 30 chars of text before it)] for every time mentioned."""
    clean = _SLOTREF.sub(" ", _REF.sub(" ", text))
    out = []
    for m in _TIME.finditer(clean):
        before = clean[max(0, m.start() - 30):m.start()]
        if m.group(4):  # noon
            out.append((now.astimezone(IST).replace(hour=12, minute=0, second=0, microsecond=0), before))
            continue
        h, mm, ampm = int(m.group(1)), m.group(2), m.group(3)
        after = clean[m.end():m.end() + 10]
        if _NOT_TIME_AFTER.match(after) or _NOT_TIME_BEFORE.search(before):
            continue
        if not mm and not ampm and not _BARE_OK_BEFORE.search(before):
            continue  # a bare "2" is not a time unless it follows after/by/at/...
        if h > 23 or (mm and int(mm) > 59):
            continue
        out.append((_resolve_hour(h, int(mm or 0), ampm, now), before))
    return out


def _relative_delay(t: str) -> int | None:
    m = re.search(r"(late|delayed?)\s+(by\s+)?(about\s+|around\s+)?(\d+|one|two|three|four|half an|an|a)\s*"
                  r"(hours?|hrs?|minutes?|mins?)", t, re.I) or \
        re.search(r"(\d+|one|two|three|half an|an)\s*(hours?|hrs?|minutes?|mins?)\s+(late|delay)", t, re.I)
    if not m:
        return None
    groups = [g for g in m.groups() if g]
    num = next(g for g in groups if g.lower() in WORD_NUM or g.isdigit())
    unit = next(g for g in groups if re.match(r"(hour|hr|min)", g, re.I))
    n = float(num) if num.isdigit() else WORD_NUM[num.lower()]
    return int(n * 60) if unit.lower().startswith("h") else int(n)


def _reason(t: str) -> str | None:
    table = [("BREAKDOWN", r"breakdown|broke down|tyre|tire|puncture|repair|engine"),
             ("TRAFFIC", r"traffic|jam|toll|congestion"), ("WEATHER", r"rain|fog|storm|flood|weather"),
             ("LOADING_DELAY", r"loading|released me late|\borigin\b"), ("ROUTE_ISSUE", r"diversion|route|detour"),
             ("LATE_DEPARTURE", r"left late|late departure|started late")]
    for code, pat in table:
        if re.search(pat, t, re.I):
            return code
    return None


def _choice(t: str) -> int | None:
    t = t.strip().lower()
    m = re.search(r"\boption\s*#?\s*(\d)\b", t) or re.fullmatch(r"\(?([1-5])\)?[.!]?", t) \
        or re.search(r"\b(?:take|book|hold|want|go with)\s+(?:number\s+)?(\d)\b", t)
    if m:
        return int(m.group(1)) - 1
    for word, idx in (("first", 0), ("1st", 0), ("second", 1), ("2nd", 1), ("third", 2), ("3rd", 2)):
        if re.search(rf"\b{word}\s+(one|option|slot)\b|\bthe {word}\b(?!\s+possible)", t):
            return idx
    if re.search(r"\b(take|book|hold)\s+(it|that|this)\b|\bthat one\b|\bthat works\b", t):
        return 0
    return None


def extract(text: str, now: datetime) -> DriverFacts:
    t = text.strip()
    low = t.lower()
    f = DriverFacts()

    ref = _REF.search(t)
    if ref:
        f.shipment_ref = ref.group(1).upper()
    f.safety_flag = bool(re.search(r"accident|injur|hurt|fire|emergency|unsafe|ambulance|crash|smoke|robbery|"
                                   r"chest pain|unwell|attack", low))
    f.delay_reason_code = _reason(low)

    # ---- times, classified by the words just before them ----
    times = find_times(t, now)
    leaving_sentence = re.search(r"leave|gate[- ]?out|finish|done|must be out|exit|another pickup", low)
    arrivals = []
    for dt, before in times:
        b = before.lower()
        if re.search(r"between\s*$", b) or (arrivals and re.search(r"between", b) and re.search(r"\b(and|to|-)\s*$", b)):
            arrivals.append((dt, "range"))
        elif re.search(r"(after|from|not before)\s*$", b) or \
                re.search(r"(book|move me to|change to|shift to|slot at|instead)[^.?!]{0,12}$", b) or \
                (re.search(r"\bbook\b|instead", low) and not re.search(r"reach|eta|arriv", low)):
            f.earliest_wanted_ts = iso(dt)
        elif _MISSED_SLOT.search(b):
            continue  # "won't make 12:00" names the OLD slot, not a new arrival time
        elif re.search(r"(before|by|till|until)\s*$", b) and leaving_sentence:
            f.latest_finish_ts = iso(dt)
        else:
            arrivals.append((dt, "point"))
    ranged = [d for d, k in arrivals if k == "range"]
    if len(ranged) >= 2:
        f.arrival_range_start, f.arrival_range_end, f.confidence = iso(ranged[0]), iso(ranged[1]), "LOW"
    elif arrivals:
        f.arrival_ts = iso(arrivals[0][0])
        f.confidence = "LOW" if re.search(r"maybe|probably|might|not sure|approx", low) else \
            ("MEDIUM" if re.search(r"around|about|approx|~", low) else "HIGH")

    if f.latest_finish_ts or re.search(r"gate[- ]?out|unload(ing)? (must )?(finish|end)|finish unloading", low):
        if re.search(r"gate[- ]?out|leave the gate|exit the gate|out of the gate", low):
            f.latest_finish_kind = "GATE_OUT"
        elif re.search(r"unload(ing)? (must )?(finish|end|be done)|finish unloading|unload end|unloading end", low):
            f.latest_finish_kind = "UNLOAD_END"
        elif f.latest_finish_ts:
            f.latest_finish_kind = "UNKNOWN"

    if not f.arrival_ts and not f.arrival_range_end:
        f.relative_delay_min = _relative_delay(t)
        if re.search(r"repair", low) and re.search(r"hour|min", low) and f.relative_delay_min is None:
            f.is_repair_time_only = True

    # ---- intent ----
    choice = _choice(t)
    if f.safety_flag:
        f.intent = "OTHER"
    elif re.fullmatch(r"(yes|yep|yeah|ok|okay|sure|confirm|confirmed\??|go ahead|book it|do it|please confirm|"
                      r"yes,? (please )?confirm( it)?|ok,? confirm( it)?|yes please)[.! ]*", low):
        f.intent = "CONFIRM"
    elif re.fullmatch(r"(no|nope|cancel|release( it)?|don'?t book|not needed|no thanks)[.! ]*", low):
        f.intent = "DECLINE"
    elif choice is not None and not times:
        f.intent, f.chosen_option_index = "CHOOSE_OPTION", choice
    elif re.search(r"still active|status|has a new dock been assigned|is my .* slot|is it (confirmed|booked)|am i (confirmed|booked)|what is my slot", low):
        f.intent = "CHECK_STATUS"
        f.arrival_ts = None  # "is my 4 PM slot active?" mentions a time, but it is not an ETA
    elif re.search(r"already at the gate|arrived early|reached early|i am early|i'm early", low):
        f.intent = "EARLY_ARRIVAL"
    elif re.search(r"late|delay|traffic|stuck|breakdown|tyre|reach|eta|arriv|miss|won'?t make|released me", low):
        f.intent = "REPORT_DELAY"
    elif re.search(r"slot|dock|possible|available|next|where (should|do) i|broken|any time|book|instead", low):
        f.intent = "ASK_SLOT_OPTIONS"
    return f
