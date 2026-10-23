# Build context: repository root (the backend imports the simulator package for seeding).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/backend

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend backend
COPY simulator simulator
COPY knowledge-base knowledge-base

RUN useradd --create-home --uid 10001 opspilot
USER opspilot
WORKDIR /app/backend

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Apply migrations, load demo data on first start (idempotent), then serve.
CMD ["sh", "-c", "alembic upgrade head && python -m app.scripts.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
