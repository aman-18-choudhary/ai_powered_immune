# Build from the repo root: docker build -f services/graph_twin/Dockerfile .
FROM python:3.11-slim
WORKDIR /app
COPY contracts ./contracts
COPY libs/svckit ./libs/svckit
COPY services/graph_twin ./services/graph_twin
RUN pip install --no-cache-dir ./contracts ./libs/svckit "./services/graph_twin[postgres]"
CMD ["python", "-m", "graph_twin"]
