FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Local development (docker compose): dev tools included, code mounted from the host and
# reloaded on every change, so nothing is copied in.
FROM base AS dev
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# Production (the default target).
FROM base AS prod
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY . .

RUN useradd --create-home app
USER app

# Cloud Run sets PORT. One worker per container; Cloud Run scales containers instead.
CMD ["sh", "-c", "exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 --timeout 0"]
