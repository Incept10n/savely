# Savely

A simple money-spending tracker. Two tabs: **Spends** (quick add with current date) and **History** (add with a specific date, edit, delete).

## Stack

- **Backend**: Flask (Python), MySQL, gunicorn — port 5000
- **Frontend**: React + Vite, served by nginx — port 80
- **Auth**: a shared access string (`AUTH_STRING`) set via env/secret; every `/api` request must send it as the `X-Auth-String` header

## Repo layout

```
backend/   Flask API + tests + Dockerfile
frontend/  React SPA + nginx.conf + Dockerfile
.github/workflows/   CI/CD (build+push + kubectl rollout restart)
```

## Backend API

Base path `/api` (except health). All `/api/spends` and `/api/auth/verify` require `X-Auth-String`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health (no auth) |
| GET | `/api/auth/verify` | Validate the auth string |
| GET | `/api/spends` | List spends (date desc) |
| POST | `/api/spends` | Add spend `{amount, comment?, date?}` — `date` defaults to now |
| PUT | `/api/spends/:id` | Update `{amount, comment?, date?}` |
| DELETE | `/api/spends/:id` | Delete |

A spend row: `amount` (number), `comment` (string), `date` (ISO datetime).

## Backend env vars

| Var | Default | Purpose |
|-----|---------|---------|
| `AUTH_STRING` | `changeme` | Access string |
| `DB_TYPE` | `mysql` | `mysql` or `sqlite` (for tests) |
| `DB_FILE` | | SQLite file path (tests) |
| `DB_HOST` | `127.0.0.1` | MySQL host |
| `DB_PORT` | `3306` | MySQL port |
| `DB_USER` | `savely` | MySQL user |
| `DB_PASSWORD` | | MySQL password |
| `DB_NAME` | `savely` | MySQL database |
| `INIT_DB` | `1` | Auto-create table on startup |

## Local dev

Backend:
```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export AUTH_STRING=mysecret DB_HOST=127.0.0.1 DB_PORT=3306 \
       DB_USER=savely DB_PASSWORD=... DB_NAME=savely
python -m pytest -q          # run tests
gunicorn --bind 0.0.0.0:5000 wsgi:app
```

Frontend (dev server proxies `/api` to `localhost:5000`):
```bash
cd frontend
npm install
npm run dev
```

## Tests

```bash
cd backend && . .venv/bin/activate && python -m pytest -q
```

## CI/CD

Push to `master` triggers path-filtered workflows that build and push Docker images
(`incept1on/savely:back` / `:front`) and `kubectl rollout restart` the deployment in the `savely` namespace.
