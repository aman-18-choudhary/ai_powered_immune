# Build from the repo root: docker build -f services/antibody_hub/Dockerfile .
FROM python:3.11-slim
WORKDIR /app
COPY contracts ./contracts
COPY libs/svckit ./libs/svckit
COPY services/antibody_hub ./services/antibody_hub
RUN pip install --no-cache-dir ./contracts ./libs/svckit "./services/antibody_hub[postgres]"
CMD ["python", "-m", "antibody_hub"]
