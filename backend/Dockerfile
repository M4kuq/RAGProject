FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_SYSTEM_PYTHON=1 \
    UV_NO_CACHE=1

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY backend/pyproject.toml /app/pyproject.toml
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb')); print('\n'.join(p['project']['dependencies']))" > /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt

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
USER root
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb')); print('\n'.join(p['project']['optional-dependencies']['dev']))" > /tmp/dev-requirements.txt \
    && uv pip install --system -r /tmp/dev-requirements.txt
USER app
