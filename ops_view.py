"""Optional ops view (Streamlit): the slot board a planner would watch + a driver chat panel.

  pip install -r requirements-ops.txt && streamlit run ops_view.py
It calls the SAME handle_turn() as the CLI, so it's just a window onto the same engine + DB.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pandas as pd
import streamlit as st

from setuhaul import config, db
from setuhaul.agent import handle_turn
from setuhaul.clock import hhmm, iso, parse_ts

st.set_page_config(page_title="SetuHaul ops view", layout="wide")
if not config.WORKING_DB.exists():
    st.error("Run `make reset-db` first.")
    st.stop()
conn = db.connect(config.WORKING_DB)


def board(now) -> pd.DataFrame:
    """Dock x start-time grid: who has each slot right now."""
    rows = conn.execute("SELECT sl.slot_id, d.dock_code, sl.slot_start_ts, sl.slot_status FROM appointment_slots sl "
                        "JOIN docks d ON d.dock_id = sl.dock_id WHERE sl.facility_id='FAC-JAI-01'").fetchall()
    appts = {r["slot_id"]: r["shipment_id"] for r in conn.execute(
        "SELECT slot_id, shipment_id FROM appointments WHERE appointment_status IN "
        "('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')")}
    holds = {}
    for h in conn.execute("SELECT * FROM slot_holds WHERE status IN ('HELD','CONFIRMED')"):
        if h["status"] == "CONFIRMED" or parse_ts(h["expires_at"]) > now:
            holds[h["slot_id"]] = f"{h['shipment_id']} ({h['status'].lower()})"
    grid: dict = {}
    for r in rows:
        cell = holds.get(r["slot_id"]) or appts.get(r["slot_id"]) or ("BLOCKED" if r["slot_status"] != "OPEN" else "free")
        grid.setdefault(hhmm(r["slot_start_ts"]), {})[r["dock_code"]] = cell
    return pd.DataFrame(grid).T.sort_index().fillna("-")


st.title("SetuHaul: dock slots + driver exception agent")
left, right = st.columns([3, 2])
now_str = right.text_input("Simulated time (IST)", "2026-08-04T10:02:00+05:30")
now = parse_ts(now_str)

with left:
    st.subheader("Jaipur DC slot board (live from working.db)")
    st.dataframe(board(now), width="stretch", height=560)
    st.subheader("Escalations (simulated outbox)")
    esc = conn.execute("SELECT case_id, shipment_id, reason_code, owner_contact_id, delivery_status, handoff_json "
                       "FROM escalations ORDER BY created_at DESC").fetchall()
    for e in esc:
        with st.expander(f"{e['case_id']} - {e['shipment_id']} - {e['reason_code']} -> {e['owner_contact_id']}"):
            st.json(json.loads(e["handoff_json"]))
    if not esc:
        st.caption("none yet")

with right:
    st.subheader("Driver chat")
    driver = st.selectbox("Driver", [r[0] for r in conn.execute(
        "SELECT DISTINCT driver_id FROM shipments WHERE driver_id NOT LIKE 'DRV_T%' ORDER BY 1")], index=0)
    session = f"ops-{driver}"
    hist = st.session_state.setdefault(session, [])
    for role, text in hist:
        st.chat_message(role).write(text)
    msg = st.chat_input("Type as the driver...")
    if msg:
        offset = timedelta(seconds=30 * len(hist))
        rep = handle_turn(conn, session_id=session, driver_id=driver, text=msg, now=iso(now + offset))
        tools = "\n".join(f"- `{t['tool']}` -> {'ok' if t['ok'] else t['error']}" for t in rep.tool_calls)
        hist += [("user", msg), ("assistant", rep.text + (f"\n\n{tools}" if tools else "") + f"\n\n_{rep.outcome}_")]
        st.rerun()
