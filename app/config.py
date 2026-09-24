"""Runtime settings, read once from the environment (and .env if present)."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast, get_args

from dotenv import load_dotenv

from app.events import TokenPrices

PROJECT_ROOT = Path(__file__).resolve().parent.parent

Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class Settings:
    gcp_project: str
    model: str = "claude-opus-5-5"
    # List prices for that model, used to estimate cost per answer (5-minute cache writes).
    prices: TokenPrices = TokenPrices(input=4.0, output=20.0, cache_write=5.0, cache_read=0.20)
    # How much the model thinks (low | medium | high | xhigh | max). Opus 5.5 defaults to medium;
    # writing correct SQL over nested GA4 data benefits from high.
    effort: Effort = "high"
    # Hard cap per query. The heaviest example (landing pages over 3 months) scans ~1.5 GB.
    max_bytes_billed: int = 2 * 1024**3
    # Rows returned to the model per query; keeps tool results small and cheap.
    max_result_rows: int = 200
    # Model round-trips allowed per user message before the agent gives up.
    max_agent_steps: int = 12
    # SQLite file holding conversation history (created on first run).
    database_path: Path = PROJECT_ROOT / "data" / "app.db"


def load_settings() -> Settings:
    load_dotenv()
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set (see .env.example)")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set (see .env.example)")
    effort = os.environ.get("CLAUDE_EFFORT", Settings.effort)
    if effort not in get_args(Effort):
        raise RuntimeError(f"CLAUDE_EFFORT must be one of {', '.join(get_args(Effort))}")
    return Settings(gcp_project=project, effort=cast(Effort, effort))
