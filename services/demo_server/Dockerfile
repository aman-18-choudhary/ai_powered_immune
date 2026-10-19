# syntax=docker/dockerfile:1
# Build from the repository root (the context must contain contracts/, libs/, services/, web-console/):
#   docker build -f services/demo_server/Dockerfile -t scam-demo-server .
#   docker run --rm -p 127.0.0.1:8080:8080 -e DEMO_JWT_SECRET=<32+ bytes> scam-demo-server
# Three stages: (1) build the web console with Node 20, (2) build a Python venv with the real
# service code, (3) a slim runtime that serves the API, the stream and the built console.

FROM node:20-slim AS console
WORKDIR /console
COPY web-console/package.json web-console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web-console/ ./
RUN npm run build

FROM python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
COPY contracts ./contracts
COPY libs/svckit ./libs/svckit
COPY services/sim_engine ./services/sim_engine
COPY services/call_guard ./services/call_guard
COPY services/txn_guard ./services/txn_guard
COPY services/antibody_hub ./services/antibody_hub
COPY services/evidence_ledger ./services/evidence_ledger
COPY services/geo_intel ./services/geo_intel
COPY services/citizen_shield ./services/citizen_shield
COPY services/live_audio ./services/live_audio
COPY services/complaints ./services/complaints
COPY services/demo_server ./services/demo_server
# live_audio is installed WITHOUT its [stt] extra: the image has no Whisper model, so the live
# microphone feature reports STT "unavailable" (never a low risk). To enable it, install
# faster-whisper and mount a pre-downloaded model cache (see services/live_audio/README.md).
# complaints (mounted at /api/complaints) keeps its SQLite file and evidence under
# /home/app/.data/complaints unless COMPLAINTS_DATABASE_URL / COMPLAINTS_EVIDENCE_DIR are set;
# mount a volume there to keep them across container restarts.
RUN pip install ./contracts ./libs/svckit ./services/sim_engine ./services/call_guard \
    ./services/txn_guard ./services/antibody_hub ./services/evidence_ledger ./services/geo_intel ./services/citizen_shield \
    ./services/live_audio "./services/complaints[analysis]" ./services/demo_server

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH \
    PORT=8080 HOST=0.0.0.0 DEMO_STATIC_DIR=/app/console
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app
COPY --from=builder /opt/venv /opt/venv
COPY --from=console /console/dist /app/console
COPY infra/docker/healthcheck.py /usr/local/bin/healthcheck.py
USER app
WORKDIR /home/app
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=4s --start-period=30s --retries=5 \
  CMD ["python", "/usr/local/bin/healthcheck.py", "/api/health"]
CMD ["python", "-m", "demo_server"]
