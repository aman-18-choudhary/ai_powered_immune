# Build from the repo root: docker build -f services/gateway/Dockerfile .
FROM python:3.11-slim
WORKDIR /app
COPY libs/svckit ./libs/svckit
COPY services/gateway ./services/gateway
RUN pip install --no-cache-dir ./libs/svckit ./services/gateway
# Behind a reverse proxy set FORWARDED_ALLOW_IPS to the proxy address so login rate limiting
# sees real client IPs (see gateway/main.py docstring).
CMD ["python", "-m", "gateway"]
