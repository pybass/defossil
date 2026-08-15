"""Entry point: defossil runs the web dashboard."""

import argparse
import logging
from importlib.metadata import version
from pathlib import Path

import uvicorn

from defossil.core.core import Core
from defossil.web.app import create_app


def main() -> None:
    """Parse arguments and run the web dashboard."""
    parser = argparse.ArgumentParser(prog="defossil", description="Improve your English by reviewing your chats with AI agents.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('defossil')}")
    parser.add_argument("--port", type=int, default=3677, help="dashboard port on 127.0.0.1")
    parser.add_argument(
        "--data-dir", type=Path, default=Core.DEFAULT_DATA_DIR, help="data root (default: ~/.local/share/defossil)"
    )
    args = parser.parse_args()
    # uvicorn configures its own loggers and leaves the root one bare, which would drop every line the workers log.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("defossil").info(f"dashboard: http://127.0.0.1:{args.port}")
    # log_config=None routes uvicorn's records to our root handler; warnings and errors still show, its chatter does not.
    uvicorn.run(
        create_app(Core(args.data_dir)),
        host="127.0.0.1",
        port=args.port,
        log_config=None,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
