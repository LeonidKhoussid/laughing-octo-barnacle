FROM node:20-bookworm-slim AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim AS semantic-models

WORKDIR /build
COPY configs/semantic-models.json ./configs/semantic-models.json
COPY scripts/download_semantic_models.py ./scripts/download_semantic_models.py
RUN python scripts/download_semantic_models.py --model-dir /models/semantic

FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PII_VAULT_BACKEND=memory \
    PII_WORKERS=1 \
    PII_MAX_BODY_BYTES=2097152 \
    PII_MAX_IN_FLIGHT=8 \
    PII_NER_THREADS=4 \
    PII_MAX_PROCESSING_SECONDS=9

COPY pyproject.toml README.md ./
COPY app ./app
COPY configs ./configs
COPY scripts ./scripts
RUN pip install .
COPY --from=semantic-models /models/semantic ./models/semantic
COPY --from=frontend-build /frontend/dist ./frontend/dist

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers \"${PII_WORKERS:-1}\" --timeout-graceful-shutdown 30"]
