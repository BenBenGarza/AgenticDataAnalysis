# GA4 Ecommerce Analysis Agent

A conversational data analysis app over the
[GA4 web ecommerce demo dataset](https://developers.google.com/analytics/bigquery/web-ecommerce-demo-dataset).
Users ask questions in a chat; an agent queries BigQuery and answers with charts and a written narrative.

> Work in progress. So far: BigQuery access, dataset profiling, and validated example queries.
> The agent loop and chat UI are next.

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

3. **Configure:** copy `.env.example` to `.env` and set `GOOGLE_CLOUD_PROJECT`.

4. **Check the connection:**

   ```bash
   GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID .venv/bin/python scripts/check_bigquery.py
   ```

## Project layout

```
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
