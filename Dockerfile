# ── Build stage ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for psycopg2, web3 (gcc, python headers)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev python3-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Runtime stage ────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=TradingWeb.settings

WORKDIR /app

# Runtime libs only (no gcc)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy project
COPY . /app

# Create dirs for logs, cache, static
RUN mkdir -p /app/logs /app/cache /app/staticfiles

# Collect static files (for production serving)
RUN python manage.py collectstatic --noinput 2>/dev/null || true

# Non-root user
RUN groupadd -r sniper && useradd -r -g sniper -d /app sniper && \
    chown -R sniper:sniper /app
USER sniper

# Daphne ASGI server on port 8000
EXPOSE 8000

# Health check — hit Django's /admin/ (returns 302 = alive)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Default: run Daphne
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "TradingWeb.asgi:application"]
