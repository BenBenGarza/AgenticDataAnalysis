"""Composition root: builds the application's objects once and wires them together.

Everything else receives its dependencies instead of constructing them, so this is the only
place that knows the concrete classes, and tests can substitute fakes anywhere below it.
"""

import anthropic
import google.auth
from google.auth import exceptions as auth_exceptions
from google.cloud import bigquery

from app.agent import Agent
from app.bigquery import BigQueryRunner
from app.config import ConfigError, Settings
from app.events import Message
from app.prompts import build_system_prompt
from app.session import ChatSession
from app.storage import ConversationStore
from app.tools.base import Tool
from app.tools.create_chart import CreateChartTool
from app.tools.run_sql import RunSqlTool


def build_session(settings: Settings) -> ChatSession:
    client = anthropic.Anthropic()
    runner = BigQueryRunner(
        _bigquery_client(settings), settings.max_bytes_billed, settings.max_result_rows
    )
    tools: list[Tool] = [RunSqlTool(runner), CreateChartTool()]
    system_prompt = build_system_prompt()

    def new_agent(messages: list[Message]) -> Agent:
        return Agent(settings, client, tools, system_prompt, messages)

    return ChatSession(ConversationStore(settings.database_path), new_agent, settings.model)


def _bigquery_client(settings: Settings) -> bigquery.Client:
    """Credentials from .env (GOOGLE_CREDENTIALS_BASE64) if given, else the gcloud login."""
    try:
        if settings.google_credentials is not None:
            # No scopes here: the BigQuery client adds its default scopes to credentials that need
            # them (service accounts); a gcloud login refuses scopes it wasn't granted with.
            credentials, _ = google.auth.load_credentials_from_dict(settings.google_credentials)
            return bigquery.Client(project=settings.gcp_project, credentials=credentials)
        return bigquery.Client(project=settings.gcp_project)
    except (auth_exceptions.DefaultCredentialsError, OSError, ValueError) as exc:
        raise ConfigError(
            "No usable Google Cloud credentials. Set GOOGLE_CREDENTIALS_BASE64 in .env "
            f"(see README), or run `gcloud auth application-default login`. ({exc})"
        ) from None
