# ai-labs-problem-discovery
A system that reads thousands of public complaints (Reddit posts, app reviews, tweets), throws away the junk, groups the complaints that describe the same problem, scores each group, and shows the top 10 problems with a rough revenue range for solving each one. It’s a “problem discovery engine” for founders.

## Local backend verification

Use a supported Python interpreter and a local virtual environment. Install the
dependencies from `requirements.txt`, configure provider credentials locally
without displaying or committing them, then start the development server:

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

Provider-backed ingestion, Gemini analysis, and pipeline endpoints can incur
costs. Do not use them as local smoke tests unless the provider, account,
limits, and requested input have been reviewed. See
[`docs/OPERATOR_API.md`](docs/OPERATOR_API.md) for bounded API verification
and run-scoped pipeline behavior.

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
