# Imagem do Emissor em Planilha. Use pelo docker-compose.yaml: docker compose up -d

# --- 1. Página (React + Vite) ---
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Servidor (Python 3.12 + uv) ---
FROM python:3.12-slim

# Liberation Sans tem as mesmas métricas da Arial exigida pela NT 008 no DANFSe.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-liberation tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo \
    EMISSOR_DATA_DIR=/data \
    PATH="/app/backend/.venv/bin:$PATH"

# O uv só é usado no build (montado, não fica na imagem).
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.12,source=/uv,target=/bin/uv \
    uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
RUN --mount=from=ghcr.io/astral-sh/uv:0.12,source=/uv,target=/bin/uv \
    uv sync --locked --no-dev
COPY --from=frontend /app/frontend/dist /app/frontend/dist

VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"
CMD ["emissor"]
