"""Service entrypoint: serve the call-guard HTTP API (and consumer when KAFKA_BOOTSTRAP is set)."""

import logging
import os

import uvicorn

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "call_guard.api:create_service_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
