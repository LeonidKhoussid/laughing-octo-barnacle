FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY configs ./configs
COPY scripts ./scripts

RUN pip install --no-cache-dir -e ".[dev]"
RUN python scripts/download_semantic_models.py

EXPOSE 8000

ENV PII_VAULT_BACKEND=memory \
    PII_WORKERS=1 \
    PII_MAX_BODY_BYTES=1048576 \
    PII_MAX_IN_FLIGHT=64

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
