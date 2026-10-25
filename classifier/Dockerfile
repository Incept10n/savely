FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev --no-cache 

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY knn.py schemas.py ./

ENV PATH="/app/.venv/bin:$PATH" \
    HF_HUB_DISABLE_SYMLINKS_WARNING="true" \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "knn:app", "--host", "0.0.0.0", "--port", "8000"]
