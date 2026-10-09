# Build from the repo root: docker build -f services/evidence_ledger/Dockerfile .
FROM python:3.11-slim
WORKDIR /app
COPY contracts ./contracts
COPY libs/svckit ./libs/svckit
COPY services/evidence_ledger ./services/evidence_ledger
RUN pip install --no-cache-dir ./contracts ./libs/svckit "./services/evidence_ledger[postgres]"
CMD ["python", "-m", "evidence_ledger"]
