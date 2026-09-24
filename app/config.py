"""Runtime settings, read once from the environment (and .env if present)."""

import base64
import binascii
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast, get_args

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
    # A Google credentials file's contents (service-account key or gcloud login), from
    # GOOGLE_CREDENTIALS_BASE64. None means use the machine's gcloud login instead.
    # repr=False keeps the secret out of logs and tracebacks.
    google_credentials: dict[str, Any] | None = field(default=None, repr=False)


class ConfigError(Exception):
    """The app can't start with the current configuration. The message says what to fix."""


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")  # explicit: never a .env found elsewhere on the machine
    google_credentials = _decode_google_credentials(os.environ.get("GOOGLE_CREDENTIALS_BASE64"))
    # A service-account key names its project, so it's enough on its own.
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or (google_credentials or {}).get("project_id")
    if not project:
        raise ConfigError("GOOGLE_CLOUD_PROJECT is not set (see .env.example)")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ConfigError("ANTHROPIC_API_KEY is not set (see .env.example)")
    effort = os.environ.get("CLAUDE_EFFORT", Settings.effort)
    if effort not in get_args(Effort):
        raise ConfigError(f"CLAUDE_EFFORT must be one of {', '.join(get_args(Effort))}")
    return Settings(
        gcp_project=project, effort=cast(Effort, effort), google_credentials=google_credentials
    )


def _decode_google_credentials(encoded: str | None) -> dict[str, Any] | None:
    """Decode GOOGLE_CREDENTIALS_BASE64: a credentials JSON file, base64-encoded to one line
    (JSON with a multi-line private key doesn't survive .env parsing reliably; base64 does)."""
    if not encoded:
        return None
    try:
        info = json.loads(base64.b64decode(encoded.strip(), validate=True))
    except (binascii.Error, ValueError):
        # "from None": never attach the (secret) value to the traceback.
        raise ConfigError(
            "GOOGLE_CREDENTIALS_BASE64 is not a base64-encoded JSON file. Create it with "
            "scripts/encode_google_credentials.py (see README)."
        ) from None
    if not isinstance(info, dict) or info.get("type") not in ("service_account", "authorized_user"):
        raise ConfigError(
            "GOOGLE_CREDENTIALS_BASE64 must contain a service-account key or a gcloud login file."
        )
    return info
