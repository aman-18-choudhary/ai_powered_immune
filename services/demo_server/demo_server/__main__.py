"""``python -m demo_server``: one process serving the API, the stream and the console."""

import logging
import os

import uvicorn

from . import logredact
from .api import create_app


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logredact.install()
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")  # noqa: S104 - container default; set HOST=127.0.0.1 locally
    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        access_log=False,
        server_header=False,
        ws_max_size=4096,
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
    )


if __name__ == "__main__":
    main()
