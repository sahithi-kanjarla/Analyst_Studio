# Analyst Studio: the FastAPI backend serving the built React frontend, in one image.
# Build:  docker build -t analyst-studio .      Run:  docker compose up --build   (see README "Deploy")

# ---- stage 1: build the frontend ----
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: the backend, with the built frontend next to it ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MPLBACKEND=Agg
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/app backend/app
COPY sample_data sample_data
COPY --from=frontend /app/frontend/dist frontend/dist
# runs, uploads, the glossary and the AI cache live here: mount a volume so they survive a restart
RUN mkdir -p backend/data && useradd --create-home app && chown -R app:app /app/backend/data
USER app
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
