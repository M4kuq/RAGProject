FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_SYSTEM_PYTHON=1 \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /usr/local/bin/

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

# Install runtime dependencies from the lock file for reproducible builds.
COPY backend/pyproject.toml backend/uv.lock /app/
ARG BACKEND_UV_EXTRA_ARGS=""
RUN uv sync --frozen --no-install-project --no-dev $BACKEND_UV_EXTRA_ARGS

COPY backend/ /app/
RUN mkdir -p /app/storage/uploads \
    && chown -R app:app /app

USER app

FROM base AS backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -m app.scripts.healthcheck http://127.0.0.1:8000/ready database
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
HEALTHCHECK NONE
CMD ["python", "-m", "app.workers.worker_main"]

FROM base AS test
# Install dev dependencies while still root (before dropping to the app user),
# then switch to the unprivileged app user once for the rest of the stage.
USER root
RUN uv sync --frozen --no-install-project --extra dev
USER app
