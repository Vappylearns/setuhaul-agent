# SetuHaul Driver Exception Agent - common commands
PY ?= python

.PHONY: reset-db test demo scenarios chat ops clean

reset-db:            ## fresh data/working.db from the read-only snapshot
	$(PY) scripts/reset_db.py

test:                ## all tests (offline, no LLM key needed)
	$(PY) -m pytest -q

demo: scenarios      ## scripted end-to-end demo + runs/reports/demo_report.md

scenarios:
	$(PY) scripts/run_scenarios.py

chat:                ## interactive chat as the SHP1017 driver
	$(PY) -m setuhaul.cli chat --shipment SHP1017

ops:                 ## optional Streamlit ops view (pip install -r requirements-ops.txt)
	streamlit run ops_view.py

clean:
	rm -rf runs/traces/*.jsonl runs/outbox.jsonl .pytest_cache
