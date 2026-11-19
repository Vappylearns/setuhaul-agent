from pathlib import Path

from scripts.run_scenarios import run

ROOT = Path(__file__).resolve().parent.parent


def test_all_scenarios_pass_offline(tmp_path, monkeypatch):
    from setuhaul import config
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(config, "REPORT_DIR", tmp_path / "reports")
    out = run("offline", ROOT / "scenarios" / "scenarios.yaml", db_path=tmp_path / "w.db", quiet=True)
    failed = [(r["id"], r["failures"]) for r in out["results"] if r["status"] != "PASS"]
    assert not failed, failed
