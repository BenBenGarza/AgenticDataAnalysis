"""Print the .env line that gives the app Google Cloud access from a credentials JSON file.

Usage:
    python scripts/encode_google_credentials.py path/to/service-account-key.json >> .env

The output is a secret: keep it in .env (git-ignored) and never commit or paste it anywhere public.
"""

import base64
import json
import sys
from pathlib import Path

ACCEPTED_TYPES = ("service_account", "authorized_user")


def main(path: str) -> None:
    raw = Path(path).read_bytes()
    kind = json.loads(raw).get("type")
    if kind not in ACCEPTED_TYPES:
        raise SystemExit(f"{path} is not a Google credentials file (type {kind!r}).")
    print(f"GOOGLE_CREDENTIALS_BASE64={base64.b64encode(raw).decode()}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
