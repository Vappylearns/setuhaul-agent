import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.reset_db import reset  # noqa: E402
from setuhaul import booking, config, db  # noqa: E402
from setuhaul.clock import parse_ts  # noqa: E402

FIXED_NOW = parse_ts("2026-08-04T10:00:00+05:30")


@pytest.fixture(autouse=True)
def _isolate_outputs(tmp_path, monkeypatch):
    """Tests never write traces/outbox into the real runs/ folder, and never need an LLM key."""
    monkeypatch.setattr(config, "TRACE_DIR", tmp_path / "traces")
    monkeypatch.setattr(config, "OUTBOX_FILE", tmp_path / "outbox.jsonl")
    monkeypatch.setenv("LLM_MODE", "offline")


@pytest.fixture
def working_db(tmp_path):
    path = reset(tmp_path / "working.db")
    yield path
    # Integrity invariant after EVERY test that touched a working DB.
    conn = db.connect(path)
    assert booking.overlapping_active_claims(conn) == 0
    conn.close()


@pytest.fixture
def conn(working_db):
    c = db.connect(working_db)
    yield c
    c.close()
