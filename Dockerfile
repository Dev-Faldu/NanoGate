# Gateway + built dashboard. arm64. Model runtime is external (native Ollama on the GB10).
FROM node:24-bookworm-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --silent
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m spacy download en_core_web_sm
COPY backend/ backend/
COPY config/ config/
COPY scripts/ scripts/
COPY --from=ui /ui/dist frontend/dist
EXPOSE 8080
WORKDIR /app/backend
CMD ["uvicorn", "nanogate.main:create_app", "--factory", "--host", "127.0.0.1", "--port", "8080"]
