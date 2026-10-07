# Build from the repo root: docker build -f services/call_guard/Dockerfile .
FROM python:3.11-slim
WORKDIR /app
COPY contracts ./contracts
COPY libs/svckit ./libs/svckit
COPY services/call_guard ./services/call_guard
RUN pip install --no-cache-dir ./contracts ./libs/svckit ./services/call_guard
CMD ["python", "-m", "call_guard"]
