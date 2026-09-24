"""Smoke test: confirm BigQuery credentials work and explore the GA4 sample dataset.

Usage:
    GOOGLE_CLOUD_PROJECT=<project-id> .venv/bin/python scripts/check_bigquery.py
"""

import warnings

from google.cloud import bigquery

DATASET = "bigquery-public-data.ga4_obfuscated_sample_ecommerce"
MAX_BYTES_BILLED = 1 * 1024**3  # 1 GB safety cap per query

# The REST fallback is fine for small result sets; silence the Storage API hint.
warnings.filterwarnings("ignore", message="BigQuery Storage module not found")


def run(client: bigquery.Client, title: str, sql: str) -> None:
    config = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_BILLED)
    job = client.query(sql, job_config=config)
    df = job.to_dataframe()
    mb = (job.total_bytes_processed or 0) / 1024**2
    print(f"\n=== {title}  ({mb:.1f} MB scanned) ===")
    print(df.to_string(index=False))


def main() -> None:
    client = bigquery.Client()
    print(f"Connected as project: {client.project}")

    run(
        client,
        "Date range and volume",
        f"""
        SELECT
          MIN(PARSE_DATE('%Y%m%d', event_date)) AS first_day,
          MAX(PARSE_DATE('%Y%m%d', event_date)) AS last_day,
          COUNT(*) AS events,
          COUNT(DISTINCT user_pseudo_id) AS users
        FROM `{DATASET}.events_*`
    """,
    )

    run(
        client,
        "Top event types",
        f"""
        SELECT event_name, COUNT(*) AS events
        FROM `{DATASET}.events_*`
        GROUP BY event_name
        ORDER BY events DESC
        LIMIT 15
    """,
    )

    run(
        client,
        "Revenue by traffic source (Dec 2020)",
        f"""
        SELECT
          traffic_source.source AS source,
          COUNT(DISTINCT user_pseudo_id) AS purchasers,
          ROUND(SUM(ecommerce.purchase_revenue), 2) AS revenue
        FROM `{DATASET}.events_*`
        WHERE _TABLE_SUFFIX BETWEEN '20201201' AND '20201231'
          AND event_name = 'purchase'
        GROUP BY source
        ORDER BY revenue DESC
        LIMIT 10
    """,
    )

    run(
        client,
        "Most common event_params keys (nested field)",
        f"""
        SELECT p.key, COUNT(*) AS occurrences
        FROM `{DATASET}.events_*`, UNNEST(event_params) AS p
        WHERE _TABLE_SUFFIX = '20201201'
        GROUP BY p.key
        ORDER BY occurrences DESC
        LIMIT 15
    """,
    )


if __name__ == "__main__":
    main()
