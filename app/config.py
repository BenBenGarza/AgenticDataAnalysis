"""Runtime settings, read once from the environment (and .env if present)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    gcp_project: str
    model: str = "claude-opus-5-5"
    # How much the model thinks (low | medium | high | xhigh | max). Opus 5.5 defaults to medium;
    # writing correct SQL over nested GA4 data benefits from high.
    effort: str = "high"
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
    return Settings(
        gcp_project=project,
        effort=os.environ.get("CLAUDE_EFFORT", Settings.effort),
    )
