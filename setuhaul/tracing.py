"""Observability: one JSONL line per event (turn, extraction, tool call, guardrail, reply).

Zero infrastructure and greppable. Every string is PII-masked before it is written.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from . import config
from .guardrails import mask_obj


class Tracer:
    def __init__(self, session_id: str, trace_dir=None):
        self.session_id = session_id
        self.dir = trace_dir or config.TRACE_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{session_id}.jsonl"

    def log(self, event: str, turn_id: str, **data) -> None:
        line = {"wall_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "session_id": self.session_id, "turn_id": turn_id, "event": event, **mask_obj(data)}
        with self.path.open("a") as f:
            f.write(json.dumps(line, default=str) + "\n")


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self.t0) * 1000, 1)
