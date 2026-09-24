"""Run the web app: `python -m app.server [--host 127.0.0.1] [--port 8000]`."""

import argparse
import logging

import uvicorn

from app.api import create_app
from app.bootstrap import build_session
from app.config import load_settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GA4 analysis agent web app.")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 inside a container")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    uvicorn.run(create_app(build_session(load_settings())), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
