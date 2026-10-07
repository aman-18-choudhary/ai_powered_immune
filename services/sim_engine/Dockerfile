# Build from the repo root: docker build -f services/sim_engine/Dockerfile .
FROM python:3.11-slim
WORKDIR /app
COPY contracts ./contracts
COPY libs/svckit ./libs/svckit
COPY services/sim_engine ./services/sim_engine
RUN pip install --no-cache-dir ./contracts ./libs/svckit ./services/sim_engine
CMD ["python", "-m", "sim_engine"]
