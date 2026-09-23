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

Terminal chat (`--show-sql` prints each query the agent runs):

```bash
.venv/bin/python -m app.cli --show-sql
```

## How it works

`app/agent.py` implements the agent loop directly on the Anthropic Messages API (no agent framework):
stream Claude's reply, run any `run_sql` tool calls it makes, send the results back, and repeat until
it answers (bounded by `max_agent_steps`). The conversation history is kept between questions, so
follow-ups work; a failed turn is rolled back so the history always stays valid.

Guardrails on every query (`app/bigquery_tool.py`): a free BigQuery dry run rejects anything that is
not a single `SELECT` and anything that would scan more than the byte limit; the real run also sets
`maximum_bytes_billed`, and only the first rows are returned to the model. SQL errors are returned to
the model so it can fix its own query.

The system prompt (`app/prompts.py`) is built from `docs/`, so the dataset notes and example queries
have one source of truth, and it is cached with prompt caching.

## Project layout

```
app/
  agent.py                Agent loop, tool definition, streamed events
  bigquery_tool.py        Read-only query execution with dry-run guardrails
  prompts.py              System prompt assembled from docs/
  config.py               Settings from environment / .env
  cli.py                  Terminal chat
docs/
  dataset_notes.md        What the data looks like, its pitfalls and obfuscation (basis for the system prompt)
  example_queries.sql     Validated SQL for common analyses (funnel, channels, products, cohorts, ...)
scripts/
  check_bigquery.py       Connection smoke test
  validate_examples.py    Runs the example queries; --dry-run checks syntax and cost for free
```

## Cost

Every query sets `maximum_bytes_billed`. Queries that unnest `event_params` over all three months
scan ~1–1.5 GB; most others scan 50–250 MB. Use `scripts/validate_examples.py --dry-run` to see
costs without spending quota.
