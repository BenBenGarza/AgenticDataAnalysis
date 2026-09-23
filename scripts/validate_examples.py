"""Run every query in docs/example_queries.sql and report cost, row count and a preview.

Usage:
    GOOGLE_CLOUD_PROJECT=<project-id> .venv/bin/python scripts/validate_examples.py [--dry-run] [name ...]

--dry-run validates syntax and reports bytes that would be scanned, at no cost.
"""

import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

from google.cloud import bigquery

EXAMPLES_FILE = Path(__file__).resolve().parent.parent / "docs" / "example_queries.sql"
MAX_BYTES_BILLED = 2 * 1024**3
PREVIEW_ROWS = 5

# The REST fallback is fine for small result sets; silence the Storage API hint.
warnings.filterwarnings("ignore", message="BigQuery Storage module not found")


@dataclass
class Example:
    name: str
    question: str
    sql: str


def parse_examples(text: str) -> list[Example]:
    """Split the file on '-- name:' headers; everything up to the next header is one query."""
    examples = []
    for block in re.split(r"^-- name:", text, flags=re.MULTILINE)[1:]:
        header, _, body = block.partition("\n")
        question = re.search(r"^-- question:\s*(.+)$", body, flags=re.MULTILINE)
        sql = "\n".join(line for line in body.splitlines() if not line.startswith("--"))
        examples.append(Example(
            name=header.strip(),
            question=question.group(1).strip() if question else "",
            sql=sql.strip().rstrip(";"),
        ))
    return examples


def main(args: list[str]) -> int:
    dry_run = "--dry-run" in args
    selected = [a for a in args if not a.startswith("--")]

    examples = parse_examples(EXAMPLES_FILE.read_text())
    if selected:
        examples = [e for e in examples if e.name in selected]

    client = bigquery.Client()
    config = bigquery.QueryJobConfig(
        maximum_bytes_billed=MAX_BYTES_BILLED, dry_run=dry_run,
    )
    failures = 0
    total_mb = 0.0

    for example in examples:
        print(f"\n=== {example.name}: {example.question}")
        try:
            job = client.query(example.sql, job_config=config)
            df = None if dry_run else job.to_dataframe()
        except Exception as exc:  # report and continue so one bad query doesn't hide the rest
            failures += 1
            print(f"FAILED: {exc}")
            continue
        mb = (job.total_bytes_processed or 0) / 1024**2
        total_mb += mb
        if dry_run:
            print(f"OK, would scan {mb:.0f} MB")
        else:
            print(f"{mb:.0f} MB scanned, {len(df)} rows")
            print(df.head(PREVIEW_ROWS).to_string(index=False))

    verb = "would scan" if dry_run else "scanned"
    print(f"\n{len(examples) - failures}/{len(examples)} queries succeeded, {verb} {total_mb / 1024:.2f} GB total")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
