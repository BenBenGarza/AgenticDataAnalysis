# GA4 Ecommerce Analysis Agent

A conversational data analysis app over the
[GA4 web ecommerce demo dataset](https://developers.google.com/analytics/bigquery/web-ecommerce-demo-dataset).
Users ask questions in a chat; an agent queries BigQuery and answers with charts and a written narrative.

> Work in progress. So far: dataset profiling, validated example queries, and a working agent
> with a terminal interface. Charts and the web chat UI are next.

## Setup

Requirements: Python 3.11+ and the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).

1. **Authenticate with Google Cloud** (no key file needed; uses Application Default Credentials):

   ```bash
   gcloud auth application-default login
   gcloud auth application-default set-quota-project YOUR_PROJECT_ID
   ```

   The dataset is public; the free BigQuery sandbox (1 TB of queries per month) is enough.

2. **Install dependencies:**

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. **Configure:** copy `.env.example` to `.env` and set `GOOGLE_CLOUD_PROJECT` and `ANTHROPIC_API_KEY`.

4. **Check the connection:**

   ```bash
   GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID .venv/bin/python scripts/check_bigquery.py
   ```

## Usage

Web API (the chat UI will be served from the same server):

```bash
.venv/bin/python -m app.server        # http://127.0.0.1:8000, interactive API docs at /docs
```

| Endpoint | Purpose |
|---|---|
| `GET /api/session` | Active conversation and its turns (what the UI loads on open) |
| `POST /api/chat` `{"question": ...}` | Ask in the active conversation; streams Server-Sent Events: `text`, `progress`, `query_started`, `query_finished`, then `done` or `error` |
| `POST /api/session/new` | Start a new conversation |
| `GET /api/conversations` | Conversation history, newest first |
| `POST /api/conversations/{id}/open` | Continue an earlier conversation |
| `DELETE /api/conversations/{id}` | Delete (soft) a conversation |

Terminal chat (`--show-sql` prints each query the agent runs):

```bash
.venv/bin/python -m app.cli --show-sql
```

Conversations are saved to `data/app.db` (SQLite, created on first run), so quitting and restarting
continues the last conversation. In the chat: `/new`, `/list`, `/open ID`, `/delete ID`, `/help`.

Development checks (tests make no API or BigQuery calls):

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .   # lint + formatting
.venv/bin/mypy                      # type checking
```

## How it works

`app/agent.py` implements the agent loop directly on the Anthropic Messages API (no agent framework):
stream Claude's reply, run any `run_sql` tool calls it makes, send the results back, and repeat until
it answers (bounded by `max_agent_steps`). The conversation history is kept between questions, so
follow-ups work; a failed turn is rolled back so the history always stays valid.

Tools implement a small interface (`app/tools/base.py`: a name, an API definition, and
`run(input) -> outcome`), so the loop dispatches them by name and adding a tool doesn't touch it.
All objects are built once in `app/bootstrap.py` (the composition root) and passed in, which is also
how the tests substitute fakes for Claude and BigQuery.

Guardrails on every query (`app/bigquery.py`): a free BigQuery dry run rejects anything that is
not a single `SELECT` and anything that would scan more than the byte limit; the real run also sets
`maximum_bytes_billed`, and only the first rows are returned to the model. SQL errors are returned to
the model so it can fix its own query.

The system prompt (`app/prompts.py`) is built from `docs/`, so the dataset notes and example queries
have one source of truth, and it is cached with prompt caching.

Persistence (`app/storage.py`, `app/session.py`): each conversation's API message history is stored
exactly as sent, append-only, one completed turn at a time, so a reloaded conversation continues
seamlessly (and keeps hitting the prompt cache). The chat view is rebuilt from that history, so there
is a single source of truth. Deleting is a soft delete (`deleted_at`); nothing is physically removed.
The schema has a `users` table (one default user today) so multiple users need no schema change.

## Project layout

```
app/
  agent.py                The agent loop: model calls + generic tool dispatch, streamed as events
  events.py               Event types shared by agent, session, API and terminal
  tools/
    base.py               Tool interface (Protocol) + ToolOutcome / ToolError
    run_sql.py            The run_sql tool: definition, input validation, result for the model
  bigquery.py             Read-only query execution with dry-run and cost guardrails
  prompts.py              System prompt assembled from docs/
  session.py              Active conversation: connects the agent to storage
  storage.py              SQLite conversation history (users, conversations, messages)
  bootstrap.py            Composition root: builds clients once and wires everything
  api.py                  HTTP endpoints; streams agent events as Server-Sent Events
  server.py               Starts the web server
  config.py               Settings from environment / .env
  cli.py                  Terminal chat
docs/
  dataset_notes.md        What the data looks like, its pitfalls and obfuscation (basis for the system prompt)
  example_queries.sql     Validated SQL for common analyses (funnel, channels, products, cohorts, ...)
tests/                    Agent loop, tools, storage, sessions and API, with fakes (no external calls)
scripts/
  check_bigquery.py       Connection smoke test
  validate_examples.py    Runs the example queries; --dry-run checks syntax and cost for free
```

## Cost

Every query sets `maximum_bytes_billed`. Queries that unnest `event_params` over all three months
scan ~1–1.5 GB; most others scan 50–250 MB. Use `scripts/validate_examples.py --dry-run` to see
costs without spending quota.
