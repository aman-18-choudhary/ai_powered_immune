"""``python -m demo_server``: one process serving the API, the stream and the console."""

import logging
import os

import uvicorn

from .api import create_app


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")  # noqa: S104 - container default; set HOST=127.0.0.1 locally
    uvicorn.run(create_app(), host=host, port=port, access_log=False)


if __name__ == "__main__":
    main()
