FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/ ./
RUN pnpm install --frozen-lockfile && pnpm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOMEPAGE_DATA_DIR=/app/state \
    HOMEPAGE_STATIC_DIR=/app/static \
    LINKWARDEN_URL=http://linkwarden:3000 \
    LINKWARDEN_API_TOKEN_FILE=/run/secrets/linkwarden_api_token

WORKDIR /app
RUN groupadd --system homepage && useradd --system --gid homepage --home-dir /app homepage \
    && mkdir -p /app/state /app/data /app/static \
    && chown -R homepage:homepage /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend/app /app/backend/app
COPY --from=frontend-build /build/frontend/dist /app/static

USER homepage
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
