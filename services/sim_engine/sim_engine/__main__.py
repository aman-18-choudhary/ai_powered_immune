"""Service entrypoint: serve the sim-engine HTTP API."""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "sim_engine.api:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
