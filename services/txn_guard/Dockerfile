# Build from the repo root: docker build -f services/txn_guard/Dockerfile .
FROM python:3.11-slim
WORKDIR /app
COPY contracts ./contracts
COPY libs/svckit ./libs/svckit
COPY services/txn_guard ./services/txn_guard
RUN pip install --no-cache-dir ./contracts ./libs/svckit ./services/txn_guard
CMD ["python", "-m", "txn_guard"]
