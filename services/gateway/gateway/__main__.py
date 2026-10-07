"""Service entrypoint: serve the gateway."""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "gateway.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
