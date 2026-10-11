# syntax=docker/dockerfile:1
# Build from the repository root (the context must contain contracts/, libs/ and services/):
#   docker build -f services/antibody_hub/Dockerfile -t scam-antibody-hub .
# Stage 1 builds a virtualenv; stage 2 copies only the venv (no compilers, no sources).
FROM python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
COPY contracts ./contracts
COPY libs/svckit ./libs/svckit
COPY services/antibody_hub ./services/antibody_hub
RUN pip install ./contracts ./libs/svckit "./services/antibody_hub[postgres]"

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH PORT=8000
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app
COPY --from=builder /opt/venv /opt/venv
COPY infra/docker/healthcheck.py /usr/local/bin/healthcheck.py
USER app
WORKDIR /home/app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=30s --retries=5 \
  CMD ["python", "/usr/local/bin/healthcheck.py", "/healthz"]
CMD ["python", "-m", "antibody_hub"]
