"""Create data/working.db from the ORIGINAL snapshot (which is never written).

Run: python scripts/reset_db.py   (or: make reset-db)
Idempotent: it deletes and rebuilds working.db every time, so two runs give the same state.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from setuhaul import config  # noqa: E402

FALLBACK_SOURCE = Path("/home/box/Downloads/mini-project-2/data/setuhaul_freight_operations.db")

# New tables live ONLY in working.db (spec Task 0).
NEW_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS slot_holds (
  hold_id         TEXT PRIMARY KEY,
  hold_group_id   TEXT NOT NULL,          -- one option may span 2 back-to-back slots
  slot_id         TEXT NOT NULL REFERENCES appointment_slots(slot_id),
  shipment_id     TEXT NOT NULL REFERENCES shipments(shipment_id),
  session_id      TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('HELD','CONFIRMED','RELEASED','EXPIRED')),
  appointment_id  TEXT REFERENCES appointments(appointment_id),
  created_at      TEXT NOT NULL,
  expires_at      TEXT NOT NULL,
  UNIQUE (idempotency_key, slot_id)
);
-- The database itself refuses a second live claim on a slot (belt AND braces).
CREATE UNIQUE INDEX IF NOT EXISTS ux_live_claim_per_slot
  ON slot_holds(slot_id) WHERE status IN ('HELD','CONFIRMED');

CREATE TABLE IF NOT EXISTS idempotency_log (
  idempotency_key TEXT PRIMARY KEY,
  operation       TEXT NOT NULL,
  response_json   TEXT NOT NULL,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
  session_id   TEXT PRIMARY KEY,
  driver_id    TEXT NOT NULL,
  shipment_id  TEXT,
  state_json   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS escalations (
  case_id          TEXT PRIMARY KEY,
  shipment_id      TEXT,
  driver_id        TEXT NOT NULL,
  reason_code      TEXT NOT NULL,
  handoff_json     TEXT NOT NULL,
  owner_contact_id TEXT,
  delivery_status  TEXT NOT NULL CHECK (delivery_status IN ('QUEUED','DELIVERED','FAILED')),
  created_at       TEXT NOT NULL
);
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_db() -> Path:
    if config.ORIGINAL_DB.exists():
        return config.ORIGINAL_DB
    return FALLBACK_SOURCE


def reset(target: Path | None = None, *, seed: bool = True) -> Path:
    """Copy the original -> target, add our tables and SYNTHETIC rows. Returns target."""
    src = source_db()
    target = Path(target or config.WORKING_DB)
    before = sha256(src)
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    shutil.copyfile(src, target)
    target.chmod(0o644)  # the copy must be writable even if the original is read-only

    conn = sqlite3.connect(target)
    conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the single writer
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(NEW_TABLES_SQL)
    if seed:
        conn.executescript((config.ROOT / "data" / "seed_synthetic.sql").read_text())
    conn.commit()
    conn.close()

    assert sha256(src) == before, "original snapshot changed - this must never happen"
    return target


if __name__ == "__main__":
    out = reset()
    print(f"working DB ready: {out.relative_to(config.ROOT)}  (source untouched: {source_db().name})")
