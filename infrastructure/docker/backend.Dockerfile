# Build context: repository root (the backend imports the simulator package for seeding).
# Two stages: dependencies are built into a virtualenv once, and the runtime image gets only that venv and the code —
# no compiler, no pip cache, no build tools.
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY backend/requirements.txt /tmp/requirements.txt
RUN /venv/bin/pip install -r /tmp/requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/venv/bin:$PATH \
    PYTHONPATH=/app:/app/backend
RUN useradd --create-home --uid 10001 opspilot
COPY --from=build /venv /venv
WORKDIR /app
COPY --chown=root:root backend backend
COPY --chown=root:root simulator simulator
COPY --chown=root:root knowledge-base knowledge-base

# Code is owned by root and read-only to the app user; compose additionally mounts the root filesystem read-only.
USER opspilot
WORKDIR /app/backend

EXPOSE 8000 9101
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

# Apply migrations, load demo data on first start, sync the knowledge base (all idempotent), then serve.
CMD ["sh", "-c", "alembic upgrade head && python -m app.scripts.seed && python -m app.scripts.reindex && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
