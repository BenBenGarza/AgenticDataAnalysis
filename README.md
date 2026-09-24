# GA4 Ecommerce Analysis Agent

A conversational data analysis app over the
[GA4 web ecommerce demo dataset](https://developers.google.com/analytics/bigquery/web-ecommerce-demo-dataset).
Users ask questions in a chat; an agent queries BigQuery and answers with charts and a written narrative.

> Work in progress. Working: the agent, charts, persistence, the web API and the chat UI.

## Quick start

You need a **Google Cloud project** (the free BigQuery sandbox is enough; the dataset is public)
and an **Anthropic API key**. All configuration lives in `.env`, which is git-ignored and never
copied into the Docker image.

**1. Configure:** copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`, then give the app
Google Cloud access in one of two ways:

- **A service-account key in `.env`** (works with Docker and Python, and is the way to share
  access): create a service account in your project with only the **BigQuery Job User** role,
  add a JSON key for it, and append it to `.env`:

  ```bash
  python3 scripts/encode_google_credentials.py path/to/key.json >> .env
  ```

  The key names its project, so `GOOGLE_CLOUD_PROJECT` is optional here. Delete the key in the
  Cloud console when it's no longer needed.

- **Your own gcloud login** (Python only): set `GOOGLE_CLOUD_PROJECT` in `.env` and run

  ```bash
  gcloud auth application-default login
  gcloud auth application-default set-quota-project YOUR_PROJECT_ID
  ```

**2. Run it**, then open **http://localhost:8000**:

- **With Docker** (needs Docker Desktop or Docker Engine, and the key in `.env`):

  ```bash
  docker compose up --build
  ```

  Conversations are kept in a Docker volume across restarts (`docker compose down -v` deletes
  them).

- **With Python 3.12+:**

  ```bash
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/python -m app.server
  ```

  Conversations are kept in `data/app.db`.

### Troubleshooting

| Message or symptom | Fix |
|---|---|
| `Configuration error: GOOGLE_CLOUD_PROJECT is not set` (or `ANTHROPIC_API_KEY`) | Create `.env` from `.env.example` in the project folder |
| `Configuration error: No usable Google Cloud credentials` | Add `GOOGLE_CREDENTIALS_BASE64` to `.env` (step 1); with Python you can instead run `gcloud auth application-default login` |
| `Configuration error: GOOGLE_CREDENTIALS_BASE64 is not a base64-encoded JSON file` | Regenerate the line with `scripts/encode_google_credentials.py` |
| "Google Cloud credentials are missing, expired or revoked" in the chat | The key was deleted or the gcloud login expired: replace the key in `.env` or rerun the gcloud login, then restart |
| A query fails with `Access Denied` / `bigquery.jobs.create permission` | `GOOGLE_CLOUD_PROJECT` must be a project your Google account can run jobs in (usually your own) |
| "The Anthropic API key was rejected" / "out of credit" in the chat | Check the key in `.env` / add credit at console.anthropic.com |
| Port 8000 already in use | Stop the other process, or change the port (`"8080:8000"` in `docker-compose.yml`, or `--port 8080`) |

## Usage

Start the app and open **http://127.0.0.1:8000** (API docs at `/docs`):

```bash
.venv/bin/python -m app.server
```

The chat UI is plain HTML, CSS and JavaScript (no UI framework, no build step), served by the same
server from `app/static/`. It streams answers as they're written, shows each query the agent runs
(expand a card for the SQL and result rows), and keeps a conversation list; Stop cancels an answer
and nothing from it is saved. Answers are Markdown, rendered by a small renderer in
`app/static/js/markdown.js` that escapes all text before adding markup, so model output can't
inject HTML or scripts.

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

Development (Python setup above, plus dev tools; tests make no API or BigQuery calls):

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

Charts (`app/tools/create_chart.py`): the model never supplies data points. It names an earlier
query's `result_id` and the columns to plot; the backend validates that against the stored result
and builds the chart from it, so a chart can only show what BigQuery returned. The same function
rebuilds charts from the saved history when a conversation is reopened. The UI draws them with
Chart.js (pinned version with a subresource-integrity hash) using a colorblind-validated palette,
and each chart's exact rows stay visible in the query card above it.

Errors and recovery: SQL mistakes go back to the model to fix. Failures it can't fix end the turn
with a message saying what to do: Claude API problems (invalid key, no credit, rate limit,
overloaded, no connection), expired Google credentials, or a turn that couldn't be saved. A failed or
stopped turn is rolled back, so nothing half-finished is stored, and the UI offers Retry. Anything
unexpected is logged with its traceback and shown as a short message instead of breaking the
stream. The server logs one line per tool call and per turn (duration, tokens, estimated cost), and
each answer in the UI shows its duration and estimated cost.

Long conversations: every follow-up resends the conversation, and query results are most of it.
Past ~60K input tokens the API clears older tool results from what the model sees (context
editing); the stored history is untouched, so reopening and charting earlier results still work.

Stored conversations stay usable when the prompt or tools change: thinking blocks are tied to the
exact prompt and tools they were produced with, and the agent asks the API to drop stale ones
(`prefix_mismatch_behavior: "drop_block"`) instead of rejecting the conversation.

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
    create_chart.py       The create_chart tool: validates a chart against a stored query result
  history.py              Read-only helpers over the message history (tool results)
  bigquery.py             Read-only query execution with dry-run and cost guardrails
  prompts.py              System prompt assembled from docs/
  session.py              Active conversation: connects the agent to storage
  storage.py              SQLite conversation history (users, conversations, messages)
  bootstrap.py            Composition root: builds clients once and wires everything
  api.py                  HTTP endpoints; streams agent events as Server-Sent Events
  server.py               Starts the web server
  config.py               Settings from environment / .env
  cli.py                  Terminal chat
  static/                 Chat UI
    index.html, styles.css
    js/main.js            App controller: state, wiring, composer
    js/api.js             Backend calls + Server-Sent Events stream parsing
    js/chat.js            Messages, streamed answers, query cards, charts
    js/charts.js          Chart rendering (Chart.js) with the validated palette
    js/sidebar.js         Conversation list
    js/markdown.js        Safe Markdown renderer
    js/dom.js             Element helper
docs/
  decision-log.md         Assumptions, cuts, problems solved, and next steps (one page)
  dataset_notes.md        What the data looks like, its pitfalls and obfuscation (basis for the system prompt)
  example_queries.sql     Validated SQL for common analyses (funnel, channels, products, cohorts, ...)
tests/                    Agent loop, tools, storage, sessions and API, with fakes (no external calls)
scripts/                  (need requirements-dev.txt)
  check_bigquery.py       Connection smoke test
  encode_google_credentials.py  Turns a Google credentials JSON file into the .env line
  validate_examples.py    Runs the example queries; --dry-run checks syntax and cost for free
Dockerfile                Production image: Python 3.12 slim, non-root user, health check
docker-compose.yml        One-command run: .env, read-only Google credentials, data volume
```

## Cost

Every query sets `maximum_bytes_billed`. Queries that unnest `event_params` over all three months
scan ~1–1.5 GB; most others scan 50–250 MB. Use `scripts/validate_examples.py --dry-run` to see
costs without spending quota.
