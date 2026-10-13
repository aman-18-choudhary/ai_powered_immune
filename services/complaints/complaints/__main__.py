"""Service entrypoint: uvicorn with the access log OFF (the app logs method/route/status only;
uvicorn's would include query strings)."""

import logging
import os

import uvicorn

if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    uvicorn.run(
        "complaints.api:create_service_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        access_log=False,
    )
