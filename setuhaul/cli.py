"""Terminal chat with the agent - the live demo.

  python -m setuhaul.cli chat --shipment SHP1017
  python -m setuhaul.cli chat --driver DRV004 --now 2026-08-04T09:31:00+05:30

Tool calls are printed dimmed so the audience can see WHAT CODE DECIDED.
Commands inside the chat:  /wait 6  (move the simulated clock 6 minutes)   /quit
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import timedelta

from rich.console import Console

from . import config, db
from .agent import handle_turn
from .clock import iso, parse_ts

console = Console()


def default_now(conn, shipment_id: str | None) -> str:
    """Use the time of that driver's real message in the snapshot, so the demo is realistic."""
    if shipment_id:
        row = conn.execute("SELECT m.message_ts FROM chat_messages m JOIN chat_threads t USING(thread_id) "
                           "WHERE t.shipment_id=? AND m.sender_type='DRIVER' ORDER BY m.message_ts DESC LIMIT 1",
                           (shipment_id,)).fetchone()
        if row:
            return row["message_ts"]
    return config.DEFAULT_NOW


def chat(args) -> None:
    if not config.WORKING_DB.exists():
        sys.exit("No data/working.db yet. Run: make reset-db")
    conn = db.connect(config.WORKING_DB)
    driver = args.driver
    if args.shipment and not driver:
        row = conn.execute("SELECT driver_id FROM shipments WHERE shipment_id=?", (args.shipment,)).fetchone()
        if not row:
            sys.exit(f"Unknown shipment {args.shipment}")
        driver = row["driver_id"]
    if not driver:
        sys.exit("Give --shipment or --driver")
    now = parse_ts(args.now or default_now(conn, args.shipment))
    session = f"cli-{driver}-{uuid.uuid4().hex[:6]}"
    mode = args.mode or config.llm_mode()
    console.print(f"[bold]SetuHaul Driver Exception Agent[/bold]  driver={driver}  "
                  f"shipment={args.shipment or '?'}  sim-time={now:%H:%M} IST  mode={mode}")
    console.print("[dim]Type as the driver. /wait N moves the clock N minutes. /quit to exit.[/dim]\n")
    while True:
        try:
            text = console.input("[bold cyan]driver> [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not sys.stdin.isatty():
            console.print(text, markup=False)  # echo piped input so saved transcripts read well
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text.startswith("/wait"):
            mins = int(text.split()[1]) if len(text.split()) > 1 else 5
            now += timedelta(minutes=mins)
            console.print(f"[dim]clock -> {now:%H:%M} IST[/dim]")
            continue
        rep = handle_turn(conn, session_id=session, driver_id=driver, text=text, now=iso(now), mode=mode,
                          preset_shipment=args.shipment)
        for t in rep.tool_calls:
            status = "ok" if t["ok"] else f"error={t['error']}"
            console.print(f"[dim]  tool {t['tool']}({_short(t['args'])}) -> {status}[/dim]")
        for g in rep.guardrail_events:
            console.print(f"[dim yellow]  guardrail: {g}[/dim yellow]")
        if rep.dedupe_hit:
            console.print("[dim yellow]  duplicate message: replaying the earlier reply[/dim yellow]")
        console.print(f"[bold green]agent>[/bold green] {rep.text}  [dim]({rep.outcome})[/dim]\n")
        now += timedelta(seconds=30)  # a little time passes between messages
    console.print(f"[dim]trace: runs/traces/{session}.jsonl[/dim]")


def _short(args: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in (args or {}).items() if v not in (None, "") and k != "raw_text")[:90]


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="setuhaul.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chat", help="interactive driver chat")
    c.add_argument("--shipment")
    c.add_argument("--driver")
    c.add_argument("--now", help="simulated time, e.g. 2026-08-04T10:02:00+05:30")
    c.add_argument("--mode", choices=["offline", "live"])
    args = p.parse_args(argv)
    if args.cmd == "chat":
        chat(args)


if __name__ == "__main__":
    main()
