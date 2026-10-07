"""Service entrypoint: serve the txn-guard HTTP shell."""

import logging
import os

import uvicorn

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "txn_guard.api:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
