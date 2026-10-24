# syntax=docker/dockerfile:1
# Optional for TLS-intercepting proxies: docker build --secret id=ca,src=/path/ca.crt .
# --- frontend build ---
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=secret,id=ca,required=false sh -c '[ -f /run/secrets/ca ] && export NODE_EXTRA_CA_CERTS=/run/secrets/ca; npm ci'
COPY frontend/ ./
RUN npm run build

# --- backend runtime ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd -m -u 10001 app
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN --mount=type=secret,id=ca,required=false sh -c '[ -f /run/secrets/ca ] && export PIP_CERT=/run/secrets/ca; pip install .'
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY scripts /app/scripts
COPY --from=web /web/dist /app/frontend/dist
RUN mkdir -p /app/backend/keystore && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
