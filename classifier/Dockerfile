FROM astral-sh/uv:0.5-python3.10-alpine AS builder

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

FROM python:3.10-alpine

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY knn.py schemas.py ./

ENV PATH="/app/.venv/bin:$PATH" \
    HF_HUB_DISABLE_SYMLINKS_WARNING="true"

EXPOSE 8000

CMD ["uvicorn", "knn:app", "--host", "0.0.0.0", "--port", "8000"]
