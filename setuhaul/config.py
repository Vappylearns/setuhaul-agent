"""Central settings. Everything comes from env vars (or a git-ignored .env) so
no secret or machine-specific path is ever hardcoded."""
from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv is optional; it only loads a local .env file
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent

# The original snapshot is READ-ONLY. We only ever write to working.db.
ORIGINAL_DB = Path(os.getenv("SETUHAUL_SOURCE_DB", ROOT / "data" / "setuhaul_freight_operations.db"))
WORKING_DB = Path(os.getenv("SETUHAUL_DB", ROOT / "data" / "working.db"))
if not WORKING_DB.is_absolute():
    WORKING_DB = ROOT / WORKING_DB

RULES_FILE = ROOT / "rules" / "allocation_rules.yaml"
RUNS_DIR = ROOT / "runs"
TRACE_DIR = RUNS_DIR / "traces"
REPORT_DIR = RUNS_DIR / "reports"
OUTBOX_FILE = RUNS_DIR / "outbox.jsonl"

HOLD_TTL_SECONDS = int(os.getenv("HOLD_TTL_SECONDS", "300"))  # a hold lives 5 minutes
DEFAULT_NOW = os.getenv("SETUHAUL_NOW", "2026-08-04T09:30:00+05:30")


def llm_mode() -> str:
    """'offline' (deterministic, no key needed) or 'live' (OpenRouter).

    Default is live only when a key is present, so the demo never breaks for lack of a key.
    """
    mode = os.getenv("LLM_MODE", "").strip().lower()
    if mode in {"offline", "fake"}:
        return "offline"
    if mode == "live":
        return "live"
    return "live" if os.getenv("OPENROUTER_API_KEY") else "offline"


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# A sensible default tool-calling model; override with OPENROUTER_MODEL.
DEFAULT_MODEL = "openai/gpt-4o-mini"


def openrouter_model() -> str:
    return os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL
