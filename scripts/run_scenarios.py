"""Run the scripted scenarios end-to-end and write a readable demo report.

  python scripts/run_scenarios.py            # offline (deterministic, no key)
  python scripts/run_scenarios.py --live     # LLM mode (needs OPENROUTER_API_KEY)

Resets data/working.db first, then runs every scenario IN ORDER on one DB.
Writes runs/transcripts/<id>.md and runs/reports/demo_report.md. Exit code 1 if any check fails.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from scripts.reset_db import reset  # noqa: E402
from setuhaul import booking, config, db  # noqa: E402
from setuhaul.agent import handle_turn, load_state  # noqa: E402
from setuhaul.clock import parse_ts  # noqa: E402


def _offered(state: dict) -> list[dict]:
    return state.get("last_options") or []


def run(mode: str, scenario_file: Path, db_path: Path | None = None, quiet: bool = False) -> dict:
    db_path = reset(db_path or config.WORKING_DB)
    conn = db.connect(db_path)
    scenarios = yaml.safe_load(scenario_file.read_text())
    results = []
    for sc in scenarios:
        lines = [f"## {sc['id']}: {sc['title']}", "", f"_Shows: {', '.join(sc.get('shows', []))}_", ""]
        failures: list[str] = []
        outcomes = []
        for step in sc["steps"]:
            now = parse_ts(step["now"])
            if "competitor_hold" in step:  # another driver grabs capacity in between (race)
                c = step["competitor_hold"]
                opts = _offered(load_state(conn, c["of_session"], ""))
                target = opts[c["option_index"]]
                r = booking.place_hold(conn, shipment_id=c["shipment"], slot_ids=target["slot_ids"],
                                       session_id=c["session"], idempotency_key=f"comp-{sc['id']}", now=now)
                lines.append(f"> *{now:%H:%M}* **[another driver, {c['shipment']}]** holds Dock "
                             f"{target['dock_code']} {target['start_ts'][11:16]} -> {'ok' if r.ok else r.error}\n")
                continue
            rep = handle_turn(conn, session_id=step["session"], driver_id=step["driver"], text=step["text"],
                              now=now, turn_id=step.get("turn_id"), mode=mode)
            outcomes.append(rep.outcome)
            tag = " *(retry of same turn id)*" if step.get("retry") else ""
            lines.append(f"**{now:%H:%M} {step['driver']}:** {step['text']}{tag}\n")
            for t in rep.tool_calls:
                args = {k: v for k, v in (t["args"] or {}).items() if k != "raw_text" and v not in (None, "")}
                lines.append(f"    tool {t['tool']}({json.dumps(args)[:110]}) -> {'ok' if t['ok'] else t['error']}")
            if rep.tool_calls:
                lines.append("")
            if rep.dedupe_hit:
                lines.append("    dedupe_hit: replayed earlier reply, nothing new written\n")
            if rep.guardrail_events:
                lines.append(f"    guardrails: {', '.join(rep.guardrail_events)}\n")
            lines.append("**Agent:** " + rep.text.replace("\n", "  \n") + f"  \n`outcome={rep.outcome}`\n")

            # ---- expectations ----
            st = load_state(conn, step["session"], step["driver"])
            opts = _offered(st)
            if step.get("expect") and rep.outcome != step["expect"]:
                failures.append(f"{step['text'][:30]!r}: expected {step['expect']}, got {rep.outcome}")
            if step.get("expect_dedupe") and not rep.dedupe_hit:
                failures.append("expected dedupe hit")
            for sid in step.get("expect_not_offered", []):
                if any(sid in o["slot_ids"] for o in opts):
                    failures.append(f"{sid} should not be offered")
            for d in step.get("expect_not_offered_docks", []):
                if any(o["dock_code"] == d for o in opts):
                    failures.append(f"dock {d} should not be offered")
            if step.get("expect_only_docks") and any(o["dock_code"] not in step["expect_only_docks"] for o in opts):
                failures.append(f"only {step['expect_only_docks']} expected")
            if step.get("expect_first_slot") and (not opts or opts[0]["slot_ids"][0] != step["expect_first_slot"]):
                failures.append(f"first option should start with {step['expect_first_slot']}")
            if step.get("expect_text") and step["expect_text"] not in rep.text:
                failures.append(f"reply should contain {step['expect_text']!r}")
            if step.get("expect_no_tool") and any(t["tool"] == step["expect_no_tool"] for t in rep.tool_calls):
                failures.append(f"tool {step['expect_no_tool']} must not be called")
            inv = booking.overlapping_active_claims(conn, now)
            if inv:
                failures.append(f"INVARIANT BROKEN: {inv} overlapping claims")
        for chk in sc.get("checks", []):
            got = conn.execute(chk["sql"]).fetchone()[0]
            if got != chk["equals"]:
                failures.append(f"check failed: {chk['sql'][:70]}... got {got}, want {chk['equals']}")
        status = "PASS" if not failures else "FAIL"
        lines.append(f"**Result: {status}**" + ("".join(f"\n- {f}" for f in failures)))
        transcript = "\n".join(lines) + "\n"
        (config.RUNS_DIR / "transcripts").mkdir(parents=True, exist_ok=True)
        (config.RUNS_DIR / "transcripts" / f"{sc['id']}.md").write_text(transcript)
        if not quiet:
            print(transcript.replace("**", "").replace("`", ""))
            print("-" * 78)
        results.append({"id": sc["id"], "title": sc["title"], "status": status, "failures": failures,
                        "outcomes": outcomes, "transcript": transcript})

    summary = _report(conn, results, mode)
    return {"results": results, "report": summary}


def _report(conn, results: list[dict], mode: str) -> str:
    esc = conn.execute("SELECT case_id, shipment_id, reason_code, owner_contact_id, delivery_status, handoff_json "
                       "FROM escalations ORDER BY created_at").fetchall()
    booked = conn.execute("SELECT a.appointment_id, a.shipment_id, a.slot_id, a.replaced_appointment_id FROM appointments a "
                          "WHERE a.booking_source='DRIVER_CHAT' AND a.appointment_status='CONFIRMED' AND a.appointment_id LIKE 'APT-DC-%'").fetchall()
    traces = list(config.TRACE_DIR.glob("*.jsonl"))
    events = [json.loads(l) for p in traces for l in p.read_text().splitlines()]
    n_pass = sum(r["status"] == "PASS" for r in results)
    out = [f"# SetuHaul demo report ({mode} mode)", "",
           f"Generated {datetime.now():%Y-%m-%d %H:%M} IST from an actual run of `scripts/run_scenarios.py`.", "",
           f"**Scenarios: {n_pass}/{len(results)} PASS.** Integrity invariant (overlapping claims): "
           f"**{booking.overlapping_active_claims(conn)}**.", "",
           "| Scenario | Outcomes per driver turn | Result |", "|---|---|---|"]
    for r in results:
        out.append(f"| {r['id']} | {' -> '.join(r['outcomes'])} | {r['status']} |")
    out += ["", "## Measures (from traces)", "",
            f"- Driver turns: {sum(1 for e in events if e['event'] == 'turn_start')}",
            f"- Tool calls: {sum(1 for e in events if e['event'] == 'tool_call')}",
            f"- Clarifying questions asked: {sum(1 for e in events if e['event'] == 'clarify')}",
            f"- Guardrail blocks: {sum(1 for e in events if e['event'] == 'guardrail_block')}",
            f"- Duplicate / retried messages absorbed: {sum(1 for e in events if e['event'] == 'dedupe_hit')}",
            f"- Bookings committed by chat: {len(booked)}",
            f"- Escalations: {len(esc)}", "", "## Bookings committed", ""]
    for b in booked:
        out.append(f"- {b['shipment_id']}: {b['appointment_id']} on {b['slot_id']} (replaced {b['replaced_appointment_id']})")
    out += ["", "## Escalation hand-offs (simulated outbox, nothing sent)", ""]
    for e in esc:
        h = json.loads(e["handoff_json"])
        skipped = [c["address"] for c in h["contact_chain"] if c["status"] != "OK"]
        out.append(f"- **{e['case_id']}** {e['shipment_id']} `{e['reason_code']}` -> owner {e['owner_contact_id']} "
                   f"({e['delivery_status']}). Why: {'; '.join(h['why_nothing_fits'][:3])}"
                   + (f". Skipped dead route: {', '.join(skipped)}" if skipped else ""))
    out += ["", "## Transcripts", ""] + [r["transcript"] for r in results]
    text = "\n".join(out) + "\n"
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORT_DIR / "demo_report.md").write_text(text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use the LLM (needs OPENROUTER_API_KEY)")
    ap.add_argument("--file", default=str(ROOT / "scenarios" / "scenarios.yaml"))
    a = ap.parse_args()
    mode = "live" if a.live else "offline"
    for p in config.TRACE_DIR.glob("*.jsonl"):
        p.unlink()  # fresh traces for a fresh report
    config.OUTBOX_FILE.unlink(missing_ok=True)
    out = run(mode, Path(a.file))
    n_fail = sum(r["status"] == "FAIL" for r in out["results"])
    print(f"\n{len(out['results']) - n_fail}/{len(out['results'])} scenarios PASS  ->  runs/reports/demo_report.md")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
