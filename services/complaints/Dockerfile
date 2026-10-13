# syntax=docker/dockerfile:1
# Build from the repository root (the context must contain contracts/, libs/ and services/):
#   docker build -f services/complaints/Dockerfile -t scam-complaints .
# Stage 1 builds a virtualenv; stage 2 copies only the venv (no compilers, no sources).
# call-guard is installed so the in-process text scorer is importable (scikit-learn makes this
# image larger than the other API services); without it analyses are reported UNAVAILABLE.
FROM python:3.11-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
COPY contracts ./contracts
COPY libs/svckit ./libs/svckit
COPY services/call_guard ./services/call_guard
COPY services/complaints ./services/complaints
RUN pip install ./contracts ./libs/svckit ./services/call_guard "./services/complaints[postgres]"

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH PORT=8000
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app \
 && mkdir -p /var/lib/complaints/evidence && chown -R app:app /var/lib/complaints
COPY --from=builder /opt/venv /opt/venv
COPY infra/docker/healthcheck.py /usr/local/bin/healthcheck.py
USER app
WORKDIR /home/app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=30s --retries=5 \
  CMD ["python", "/usr/local/bin/healthcheck.py", "/healthz"]
CMD ["python", "-m", "complaints"]
