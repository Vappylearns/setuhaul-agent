import hashlib

from scripts.reset_db import reset, source_db
from setuhaul import db


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_original_untouched_and_reset_idempotent(tmp_path):
    before = _sha(source_db())
    p = reset(tmp_path / "w.db")
    c1 = db.connect(p)
    counts1 = [c1.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("shipments", "slot_holds", "appointments")]
    c1.close()
    p = reset(tmp_path / "w.db")
    c2 = db.connect(p)
    counts2 = [c2.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("shipments", "slot_holds", "appointments")]
    assert counts1 == counts2 == [31, 0, 20]  # 21 real + 10 SYNTHETIC shipments
    assert _sha(source_db()) == before


def test_new_tables_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"slot_holds", "idempotency_log", "conversations", "escalations"} <= names
