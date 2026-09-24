"""Composition root: builds the application's objects once and wires them together.

Everything else receives its dependencies instead of constructing them, so this is the only
place that knows the concrete classes, and tests can substitute fakes anywhere below it.
"""

import anthropic
from google.cloud import bigquery

from app.agent import Agent
from app.bigquery import BigQueryRunner
from app.config import Settings
from app.events import Message
from app.prompts import build_system_prompt
from app.session import ChatSession
from app.storage import ConversationStore
from app.tools.run_sql import RunSqlTool


def build_session(settings: Settings) -> ChatSession:
    client = anthropic.Anthropic()
    runner = BigQueryRunner(
        bigquery.Client(project=settings.gcp_project),
        settings.max_bytes_billed,
        settings.max_result_rows,
    )
    tools = [RunSqlTool(runner)]
    system_prompt = build_system_prompt()

    def new_agent(messages: list[Message]) -> Agent:
        return Agent(settings, client, tools, system_prompt, messages)

    return ChatSession(ConversationStore(settings.database_path), new_agent, settings.model)
