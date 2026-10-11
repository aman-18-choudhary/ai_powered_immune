"""Service entrypoint: uvicorn + KafkaBus (when KAFKA_BOOTSTRAP is set) + GEO_DATABASE_URL."""

import logging
import os

import uvicorn

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "geo_intel.api:create_service_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        access_log=False,  # the app logs method/route/status only; uvicorn would log query strings
    )
