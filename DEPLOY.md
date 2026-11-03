# Deploying Analyst Studio

One container, one port. It serves the API and the built pages together, keeps everything it writes under one folder, and needs no database. Any host that runs Docker, has a **persistent disk** and gives the container **at least 1 GB of memory** will do (2 GB is more comfortable for large files).

## 1. Build

```bash
git clone <your repository> analyst-studio && cd analyst-studio
docker build -t analyst-studio:1.0.0 --build-arg APP_VERSION=1.0.0 .
```

The image builds the frontend, installs the backend requirements and Chromium (used for the PDF exports), runs as a non-root user (`app`, uid 10001) and has a health check.

## 2. Run

```bash
docker run -d --name analyst-studio -p 8000:8000 \
  -v analyst-data:/data \
  --env-file .env \
  --restart unless-stopped \
  analyst-studio:1.0.0
```

With Compose: `cp .env.example .env`, edit it, then `docker compose up -d --build`.

Where a host sets the port itself (for example through a `PORT` variable), pass it through: the server listens on `PORT`.

## 3. Environment variables

Copy `.env.example` to `.env` (never commit it). The ones that matter for a public site:

| variable | example | secret? | what it does |
|---|---|---|---|
| `PORT` | `8000` | no | the port the server listens on |
| `APP_ENV` | `production` | no | no cross-origin site is allowed unless listed |
| `DATA_DIR` | `/data` | no | where everything is stored (the image sets it); mount a volume here |
| `SITE_ORIGIN` / `CORS_ORIGINS` | `https://studio.example.com` | no | only needed if the page and the API are on different origins |
| `TRUST_PROXY_HEADERS` | `1` | no | behind a proxy you control: believe `X-Forwarded-Proto` and `X-Forwarded-For` (HTTPS headers, per-visitor limits) |
| `PUBLIC_DEMO` | `on` | no | runs kept 3 days, uploads and typed Lens questions need the code, low daily caps; the sample report stays open and makes no AI call |
| `UPLOADS_REQUIRE_CODE` | `a-long-random-code` | **yes** | the access code for uploads and typed Lens questions |
| `RETENTION_DAYS` | `3` | no | days a finished run (and its uploads) is kept; `0` keeps everything |
| `DAILY_AI_RUNS` | `10` | no | AI analyses per day across all visitors (default 100, or 10 with `PUBLIC_DEMO`) |
| `DAILY_LENS_QUESTIONS` | `60` | no | Lens questions per day (default 500, or 60 with `PUBLIC_DEMO`) |
| `UPLOAD_RATE_LIMIT`, `UPLOAD_RATE_WINDOW_S` | `10`, `3600` | no | uploads one visitor may start per window |
| `GROQ_API_KEY` | `gsk_...` | **yes** | the AI provider key (or `GOOGLE_API_KEY`); with none, the app runs on rules only |
| `LLM_PROVIDER`, `LLM_MODEL` | `groq`, ... | no | which model; `none` turns the AI off |
| `AI_TPM_LIMIT` | `6000` | no | tokens per minute to stay inside (the free Groq tier) |

Example for a public demo:

```
PORT=8000
APP_ENV=production
PUBLIC_DEMO=on
UPLOADS_REQUIRE_CODE=change-this-to-a-long-random-string
TRUST_PROXY_HEADERS=1
GROQ_API_KEY=gsk_your_key
DAILY_AI_RUNS=10
DAILY_LENS_QUESTIONS=60
```

Secrets are the access code and the provider key: set them in the host's secret store or an `.env` file the host keeps, never in the image or the repository.

## 4. The volume

Mount a persistent disk at **`/data`**. Runs, uploaded files, the glossary, the AI cache and the daily counts are all under it. Without a volume they are lost whenever the container is replaced. Back it up by copying the folder while the app is idle; to wipe a site, stop the container and empty the volume.

## 5. Health check

`GET /api/health` answers `{"status":"ok","version":"..."}` and nothing else (no settings, no paths). The image has it as a `HEALTHCHECK`; point the host's health check at the same path and port.

## 6. Logs

Logs go to standard output (`docker logs -f analyst-studio`, or the host's log view). They hold request lines (method, path, status) and errors by type only: never a data value, a file's content, a question, an AI reply or a query string. A run's own record (what each agent did, with counts but no rows) is in its folder under `/data/runs/<id>/events.jsonl`; AI usage by stage is in `ai_usage.json` next to it.

## 7. Pre-launch checklist

- [ ] The image builds and the container is `healthy` (`docker ps`).
- [ ] `python scripts/smoke_live.py https://your-site YOUR_CODE` prints PASS for all five checks.
- [ ] `/data` is a persistent volume (restart the container and check a run is still there).
- [ ] `UPLOADS_REQUIRE_CODE` is set, long and random; a wrong code is refused.
- [ ] `PUBLIC_DEMO=on` for a public site; `RETENTION_DAYS` is what you want to promise visitors (the privacy note on the upload panel states it).
- [ ] HTTPS is terminated by the host or a proxy; `TRUST_PROXY_HEADERS=1` only if that proxy sets `X-Forwarded-*`.
- [ ] The proxy does not buffer `/api/runs/*/events` (the app sends `X-Accel-Buffering: no` and a keep-alive every 15 s) and allows a request body of at least `MAX_UPLOAD_MB` (50 by default).
- [ ] The provider key has a spending or rate limit of its own; the daily caps are set.
- [ ] You have read "Known limits" in the README and are happy with them.
- [ ] Try "Delete my run" on a test upload and check it is gone.

## Pinned dependency versions

`backend/requirements.txt` lists what the app needs, with minimum versions, and stays readable. `backend/constraints.txt` pins every package it installs (and what they pull in) to the exact
versions the test suite passed on, and the Dockerfile installs with `pip install -r requirements.txt -c constraints.txt`, so the host runs the same pandas, numpy and scipy as the tests.
The image uses the same Python minor version as the test machine (3.13). Different pandas versions give different statistics for the same column (for example the skew of a constant one), which is why this matters.

To update: in a clean virtual environment on the same Python, run `pip install -U -r backend/requirements.txt`, run the full test suite (`cd backend && python -m pytest tests`), and only when it passes
regenerate `backend/constraints.txt` from that environment (`pip freeze` for the packages in it, same `name==version` lines) and commit both together. Never edit a pin by hand without running the suite.

**Check that the pins install.** `python scripts/check_constraints.py` runs pip's resolver as a dry run on `requirements.txt` with `constraints.txt` (the exact Dockerfile command; nothing is installed, the network is needed) and fails with pip's explanation if two pins disagree. Run it after every edit of the pins, and before building an image. The test `backend/tests/test_constraints.py` checks the pins offline (every line exact, the langchain-core / certifi pair consistent) and runs the dry run too when `RUN_PIP_RESOLVE=1`. Past conflict: `langchain-core==1.7.4` needs `certifi>=2026.10.19`, so certifi is pinned to 2026.10.19.

## 8. Rollback

Every image is tagged with its version. To go back: stop the container, start the previous tag on the same volume.

```bash
docker stop analyst-studio && docker rm analyst-studio
docker run -d --name analyst-studio -p 8000:8000 -v analyst-data:/data --env-file .env --restart unless-stopped analyst-studio:<previous tag>
```

The data folder is plain files and the format has only been added to, so an older image reads it; copy the volume first if you want a safe point to return to. Run `scripts/smoke_live.py` again after a rollback.
