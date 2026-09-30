# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM node:22-bookworm-slim AS resolver
ENV NODE_ENV=production HOST=0.0.0.0
WORKDIR /app/ppv-hls-stream-resolver
COPY ppv-hls-stream-resolver/package*.json ./
RUN npm ci --omit=dev
COPY ppv-hls-stream-resolver/ ./
USER node
CMD ["node", "src/server.js"]

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright REDDIT_BROWSER_CHANNEL=chromium \
    OMP_THREAD_LIMIT=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg tesseract-ocr tesseract-ocr-eng ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=frontend /build/frontend/node_modules ./frontend/node_modules
RUN node frontend/node_modules/playwright/cli.js install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY --from=frontend /build/frontend/dist ./frontend/dist
COPY frontend/package.json ./frontend/package.json
COPY frontend/scripts ./frontend/scripts
COPY bigplays ./bigplays
COPY scripts ./scripts
RUN useradd --uid 10001 --create-home bigplays && mkdir /data && chown bigplays:bigplays /data
USER bigplays
CMD ["python", "-m", "bigplays.main", "server", "run", "--host", "0.0.0.0", "--port", "8000"]
