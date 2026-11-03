# ai-labs-problem-discovery

**Tatvic Labs** problem-discovery engine: **find new paid problems first, determine Tatvic delivery second.** Collect permitted public evidence (budget-holder and market signals **and** practitioner posts), cluster the same problem once, score with commercial-first RUBRIC `v0.1`, and surface India-first **leads** for Khilav Joshi — never a padded generic Top 10 with invented revenue.

**Single source of truth:** [`MASTER_SPECIFICATION.md`](MASTER_SPECIFICATION.md)
**Deadline:** 2026-10-30 · **Model policy:** local LiteLLM primary · **Default sources:** docs, Stack Exchange, Reddit, Hacker News, approved RSS. **Opt-in:** X (`ENABLE_X`, limit 50) and YouTube (`ENABLE_YOUTUBE`, Data API v3, 11-channel pilot)
**Weekly command:** `python scripts/weekly_run.py` → `data/reports/leads_YYYY-MM-DD.md` (`python -m app.leads` does not collect)
**Reports:** `data/reports/leads_YYYY-MM-DD.md` and `data/reports/opportunities_YYYY-MM-DD.md` · **Optional email:** Gmail API only (not SMTP; see master §7.1)

Policy brain: `docs/tatvic/` (SOUL, GOAL, RUBRIC, ASSETS, SOURCES, AGENTS).
Architecture tracker: [`docs/DISCOVERY_OBJECTIVE_FIX.md`](docs/DISCOVERY_OBJECTIVE_FIX.md) (P0–P2 closed). Living gaps: [`docs/IMPLEMENTATION_GAP_REPORT.md`](docs/IMPLEMENTATION_GAP_REPORT.md). Operator API notes: `docs/OPERATOR_API.md`.

## Local backend verification

Use a supported Python interpreter and a local virtual environment. Install the
dependencies from `requirements.txt`, configure provider credentials locally
without displaying or committing them, then start the development server:

**Linux / macOS** (use the project venv — system `uvicorn` does not have FastAPI):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
source .venv/bin/activate   # or: ./scripts/dev-api.sh
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Windows:**

```cmd
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

In a second Windows CMD window, verify the running build and API contract:

```cmd
curl.exe --fail-with-body http://127.0.0.1:8000/health
curl.exe --fail-with-body http://127.0.0.1:8000/openapi.json
```

`/health` returns the service name, `status: "ok"`, and the FastAPI
application version. `/openapi.json` identifies the routes and their request
and response schemas. The API does not expose provider configuration or
credentials. To run deterministic backend tests without provider calls:

```cmd
.venv\Scripts\python.exe -m pytest -q
```

Provider-backed ingestion, LiteLLM analysis (Gemini only as optional fallback),
and pipeline endpoints can incur costs. Do not use them as local smoke tests
unless the provider, account, limits, and requested input have been reviewed.
See [`docs/OPERATOR_API.md`](docs/OPERATOR_API.md) for bounded API verification
and run-scoped pipeline behavior. Product intent and acceptance live in
[`MASTER_SPECIFICATION.md`](MASTER_SPECIFICATION.md).

## Weekly discovery (leads lane)

Collect articles (and optional X/YouTube), extract leads, write the markdown report.
This is **not** the same as `python -m app.leads` (extract/report/review only).

```bash
# Bounded / no LLM
LEADS_COLLECT_LIMIT=10 LEADS_MAX_LLM_CALLS=0 python scripts/weekly_run.py

# Full weekly with opt-in X + YouTube (X is always limit=50)
ENABLE_X=true ENABLE_YOUTUBE=true LEADS_COLLECT_LIMIT=80 LEADS_MAX_LLM_CALLS=40 python scripts/weekly_run.py
```

The script does not send email and does not install cron. Reddit remains a default **pipeline** source, not a weekly_run collector.

## Local dashboard

Start the backend in one Windows CMD window using the command above. In a
second CMD window, install and start the React dashboard:

```cmd
cd /d C:\Users\singh\OneDrive\Desktop\ai-labs-problem-discovery\ai-labs-problem-discovery\frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies API requests to the backend at
`http://127.0.0.1:8000`; for a different local backend, set
`VITE_API_PROXY_TARGET` before starting Vite. `VITE_API_BASE_URL` is available
for a deployed API origin that permits browser requests. The dashboard loads
existing database results without starting collection. A pipeline run starts
only after an operator selects sources and clicks **Start run**. The X
connector requests its fixed 50-item minimum and may incur provider costs.
Provider configuration/health is not exposed by the API, so connector support
does not guarantee a live provider is ready.

Run the dashboard checks from the `frontend` directory:

```cmd
npm test
npm run build
```
