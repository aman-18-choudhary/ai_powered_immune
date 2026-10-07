# Build from the repo root: docker build -f services/gateway/Dockerfile .
FROM python:3.11-slim
WORKDIR /app
COPY libs/svckit ./libs/svckit
COPY services/gateway ./services/gateway
RUN pip install --no-cache-dir ./libs/svckit ./services/gateway
CMD ["python", "-m", "gateway"]
