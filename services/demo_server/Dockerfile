# Build from the repository root (the build context must contain contracts/, libs/ and services/):
#   docker build -f services/demo_server/Dockerfile -t demo-server .
#   docker run --rm -p 8080:8080 -e DEMO_JWT_SECRET=<32+ bytes> demo-server
FROM python:3.11-slim
WORKDIR /app
COPY contracts ./contracts
COPY libs/svckit ./libs/svckit
COPY services/sim_engine ./services/sim_engine
COPY services/call_guard ./services/call_guard
COPY services/txn_guard ./services/txn_guard
COPY services/antibody_hub ./services/antibody_hub
COPY services/evidence_ledger ./services/evidence_ledger
COPY services/demo_server ./services/demo_server
RUN pip install --no-cache-dir ./contracts ./libs/svckit ./services/sim_engine \
    ./services/call_guard ./services/txn_guard ./services/antibody_hub \
    ./services/evidence_ledger ./services/demo_server \
 && useradd --create-home --uid 10001 app
USER app
ENV PORT=8080
EXPOSE 8080
CMD ["python", "-m", "demo_server"]
