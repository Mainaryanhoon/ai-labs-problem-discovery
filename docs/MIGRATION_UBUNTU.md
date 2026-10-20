# Ubuntu migration and local verification

This guide does not contain credentials. Do not put `.env` or a database file in Git.

## Runtime requirements

- The repository does **not** pin a Python release in `requirements.txt` or a Python version file. Its `X | None` type syntax requires Python 3.10 or later. Use a supported Python 3.10+ interpreter and verify with `python3 --version`.
- `frontend/package.json` declares Node.js >=20 and `frontend/package-lock.json` is checked in. Use npm with `npm ci`.

## Install tools

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nodejs npm curl
python3 --version
node --version
npm --version
```

If the Ubuntu package repository provides Node older than 20, install a current Node 20+ release from an approved Node.js package source before proceeding. Do not continue until both runtime requirements are met.

## Clone the private repository and select the project branch

Use the machine's already-configured private GitHub authentication; do not put a token in the clone URL.

```bash
git clone https://github.com/Mainaryanhoon/ai-labs-problem-discovery.git
cd ai-labs-problem-discovery
git checkout feat/problem-discovery-foundation
git status --short --branch
```

## Create the Python environment and install backend packages

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Create a local environment file without credentials

Copy the template locally; do not display or log either environment file:

```bash
cp .env.example .env
```

For the first offline verification, leave credential values empty. Do not blank required non-secret settings such as the database URL or actor IDs; the backend needs a valid database URL, and a live collector needs its configured actor ID. The setting names declared by `app/config.py` are:

- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `DATABASE_URL`
- `APIFY_API_TOKEN`
- `APIFY_ACTOR_ID`
- `SERVICE_API_KEY`
- `SUBMISSION_RATE_LIMIT_PER_MINUTE`
- `APIFY_REDDIT_ACTOR_ID`
- `APIFY_X_ACTOR_ID`

Keep `GEMINI_API_KEY`, `APIFY_API_TOKEN`, and `SERVICE_API_KEY` empty for offline tests unless you have an approved reason to configure them. This guide intentionally includes no secret values.

## Run backend tests without the real database

Run the test suite with an in-memory SQLite URL so verification does not target the copied production/local database:

```bash
DATABASE_URL=sqlite:///:memory: .venv/bin/python -m pytest -q
```

## Copy the existing database separately (optional)

Do not transfer the database through Git. Stop the Windows backend first, then transfer the database file separately over an approved secure channel. The known local filename is `data/problem_discovery.db`; only copy the file, never paste or print its contents.

On Ubuntu, create the destination directory and substitute the actual local path, Ubuntu account/host, and repository path:

```bash
mkdir -p data
scp <source-path-to-database-file> <ubuntu-user>@<ubuntu-host>:<repository-path>/data/problem_discovery.db
```

The default `DATABASE_URL` is relative to the repository. Confirm the local setting points to the copied file before starting the API. Application startup creates missing additive tables; do not run a destructive migration or delete historical rows.

## Install and verify the frontend

```bash
cd frontend
npm ci
npm test
npm run build
```

## Start backend and frontend separately

Terminal 1, from the repository root:

```bash
cd <repository-path>
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal 2:

```bash
cd <repository-path>/frontend
npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` and `/health` to `http://127.0.0.1:8000` by default. No provider is called merely by opening the dashboard. Only click **Start run** when you intentionally authorize collection; X requires a 50-item query and can incur costs.

## Read-only API verification

In a third terminal, check the backend health and persisted run-history route:

```bash
curl --fail-with-body --silent --show-error http://127.0.0.1:8000/health
curl --fail-with-body --silent --show-error http://127.0.0.1:8000/api/pipeline/runs
```

Both should return HTTP 200. If run history returns 404, first confirm the server process was started from this checkout and inspect `http://127.0.0.1:8000/openapi.json` for the route. These GET requests do not start a pipeline or call external providers.
