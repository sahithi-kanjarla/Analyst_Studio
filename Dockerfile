# Analyst Studio: the FastAPI backend serving the built React frontend, in one image, on one port.
# Build:  docker build -t analyst-studio .        Run:  docker run -p 8000:8000 -v analyst-data:/data analyst-studio     (see DEPLOY.md)

# ---- stage 1: build the frontend ----
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: the backend, with the built frontend next to it ----
# the Python the tests ran on (see backend/constraints.txt)
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MPLBACKEND=Agg
# Chromium makes the PDF exports (briefs and the report); the fonts keep the PDFs readable
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-liberation && rm -rf /var/lib/apt/lists/*
ENV BROWSER_PATH=/usr/bin/chromium
WORKDIR /app
COPY backend/requirements.txt backend/constraints.txt backend/
# constraints.txt pins every package (and what they pull in) to the versions the tests passed on
RUN pip install --no-cache-dir -r backend/requirements.txt -c backend/constraints.txt
COPY backend/app backend/app
COPY sample_data sample_data
COPY --from=frontend /app/frontend/dist frontend/dist
ARG APP_VERSION=1.0.0
# One folder holds everything the app writes (runs and uploads, the glossary, the AI cache, the daily counts): mount a volume on /data so it survives a restart or a new image
ENV APP_VERSION=$APP_VERSION APP_ENV=production DATA_DIR=/data PORT=8000
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown -R app:app /data
VOLUME ["/data"]
USER app
WORKDIR /app/backend
EXPOSE 8000
# the port is whatever PORT says; the check calls the page's own health route (status and version only)
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'), timeout=4)" || exit 1
CMD ["python", "-m", "app"]
