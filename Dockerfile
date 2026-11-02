FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.7.17 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
COPY contracts ./contracts
COPY modules ./modules
COPY adapters ./adapters
COPY specs ./specs
COPY configs ./configs
RUN uv sync --locked --no-dev --no-editable --extra ml --extra tracking --extra monitoring --extra mcp --extra aws
ENV PATH="/app/.venv/bin:$PATH"
ENV DO_NOT_TRACK=1 MLFLOW_DISABLE_TELEMETRY=true MLFLOW_DISABLE_AGENT_HINT=1

FROM base AS tracking

FROM base AS worker
ENTRYPOINT ["python", "-m", "adapters.training.cloud_worker"]

FROM base AS inference
EXPOSE 8080
ENTRYPOINT ["uvicorn", "modules.propertypulse.inference.sagemaker:app", "--host", "0.0.0.0", "--port", "8080"]

FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
