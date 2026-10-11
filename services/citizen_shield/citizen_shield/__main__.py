"""Service entrypoint: uvicorn (access log off) with the env-configured app."""

import logging
import os

import uvicorn

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "citizen_shield.api:create_service_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        access_log=False,  # the app logs method/route/status only; uvicorn would log query strings
    )
