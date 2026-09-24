"""Run the web app: `python -m app.server [--host 127.0.0.1] [--port 8000]`."""

import argparse

import uvicorn

from app.api import create_app
from app.config import load_settings
from app.session import ChatSession
from app.storage import ConversationStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GA4 analysis agent web app.")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 inside a container")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    settings = load_settings()
    session = ChatSession(settings, ConversationStore(settings.database_path))
    uvicorn.run(create_app(session), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
