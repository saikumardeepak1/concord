# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update -y \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY concord ./concord
COPY evals ./evals
COPY web ./web

RUN pip install --upgrade pip \
 && pip install .

ENV CONCORD_HOST=0.0.0.0 \
    CONCORD_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["concord", "serve"]
