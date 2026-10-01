# AGENTS.md

**Savely** — a simple money-spending tracker. Two independent apps (`backend`, `frontend`), no root workspace build — run commands inside the app dir. Default branch is `master`. Repo: `github.com/Incept10n/savely` (public).

Live at **https://savely.inceptech.ru/** — amounts are displayed in **rubles (₽)**.

## Currency

- Prices are in **rubles**. Display uses a `₽` suffix (e.g. `999.00 ₽`), already applied in `frontend/src/HistoryTab.tsx` and input labels in `frontend/src/SpendsTab.tsx` / `HistoryTab.tsx`. Don't revert to `$`.

## Stack

- **Backend** (`backend/`): Flask + PyMySQL, run by gunicorn, port **5000**. SQLite is used only for tests.
- **Frontend** (`frontend/`): React + Vite, built to static files and served by nginx, port **80**. No tests, no typecheck; `npm run build` = `tsc -b && vite build`.
- **Database**: MySQL 8.0 running as a Kubernetes StatefulSet (`mysql-0`, PVC `mysql-data`) in namespace `savely`.

## Auth

A shared access string `AUTH_STRING` protects the API (value lives in the OpenBao `savely` secret, not in this repo). Every `/api` request **except** `/api/health` must send it as the `X-Auth-String` header. The frontend stores it in `localStorage['savely-auth']` after login. Enforced by `backend/app/auth.py` (`require_auth` decorator).

## Backend API

Base path `/api`. Routes in `backend/app/routes.py`; DB layer in `backend/app/db.py`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health (no auth) |
| GET | `/api/auth/verify` | Validate the auth string |
| GET | `/api/spends` | List spends (date desc) |
| POST | `/api/spends` | Add spend `{amount, comment?, date?}` — `date` defaults to now |
| PUT | `/api/spends/:id` | Update `{amount, comment?, date?}` |
| DELETE | `/api/spends/:id` | Delete |

A spend row: `id` (int), `amount` (float), `comment` (string), `date` (ISO datetime `YYYY-MM-DDTHH:MM:SS`).

## Backend env vars

Managed via ExternalSecret → k8s secret `savely-secret` (source: OpenBao `secret/data/savely`). Defaults:

| Var | Default | Purpose |
|-----|---------|---------|
| `AUTH_STRING` | `changeme` | Access string (real value lives in OpenBao `savely` secret) |
| `DB_TYPE` | `mysql` | `mysql` or `sqlite` (tests) |
| `DB_FILE` | | SQLite file path (tests) |
| `DB_HOST` | `127.0.0.1` | MySQL host (prod: `mysql.savely.svc.cluster.local`) |
| `DB_PORT` | `3306` | MySQL port |
| `DB_USER` | `savely` | MySQL user |
| `DB_PASSWORD` | | MySQL password |
| `DB_NAME` | `savely` | MySQL database |
| `INIT_DB` | `1` | Auto-create tables/index on startup |
| `YANDEX_AI_API_KEY` | | AI API key (real value lives in OpenBao) |
| `YANDEX_AI_FOLDER_ID` | | Cloud folder id |
| `YANDEX_AI_MODEL_URI` | `gpt://b1g22vmvppgsen3ogkj9/yandexgpt-5.1/latest` | Model URI |
| `YANDEX_AI_INPUT_PRICE_PER_1K` | `0.8` | ₽ per 1k input tokens (cost accounting only) |
| `YANDEX_AI_OUTPUT_PRICE_PER_1K` | `0.8` | ₽ per 1k output tokens (cost accounting only) |
| `YANDEX_AI_MODEL_MAX_TOKENS` | `300` | Sets `maxTokens` **and** the guard's output budget |
| `YANDEX_AI_RESPONSE_FORMAT` | `1` | Request JSON output; auto-disabled if the model rejects it |
| `YANDEX_AI_MAX_COST_PER_REQUEST` | `50` | Pre-request guard → 400 without calling the provider |
| `YANDEX_AI_DAILY_LIMIT` | `15` | Daily budget, **reported to the frontend only** |

## AI analysis (`/api/ai/*`)

- Provider is **Yandex Foundation Models** (`ai.py`, raw `requests.post`, no SDK). Model classifies the **current calendar month only** into a fixed 15-item list (`ai.CATEGORIES`); anything it invents is normalized down to `Другое`.
- **The model never echoes spend text and never does arithmetic.** It returns indices; `_bucketize` resolves them back to rows and sums `total`/`count`/`share` on the server, so buckets always add up to the month total. Don't move arithmetic into the prompt.
- Rows arrive **newest-first** (`ORDER BY spent_at DESC, id DESC`), so **index 0 is the most recent spend**. Prompt text omits dates for this reason (rows come back by index); amounts stay because the `notice` reasons about them.
- Month scoping happens in SQL (`db.list_spends_month(start, end)`, bounds from `ai.month_bounds()`), **not** in Python over a full-table read. `GET /api/spends` still returns all history on purpose — don't scope it.
- `ai._JSON_MODE` caches whether the model accepts `responseFormat`; a 400 drops it for the process. Reset it in tests.
- Cost metrics come back in `metrics` — `uniqueComments / rows` tells you whether deduplicating identical comments would actually pay off.

## Backend code gotchas (IMPORTANT)

- `backend/app/db.py` serves **both** SQLite and MySQL with dual schemas (`SCHEMA` vs `MYSQL_SCHEMA`) and placeholder differences. **PyMySQL requires `%s` placeholders, SQLite requires `?`.** SQL is written with `?` and `_execute()` translates `?` → `%s` when not SQLite. **Always write new/spread SQL with `?`** and let `_execute` handle translation.
- `init_db()` must select the correct schema for the DB type (hard-won bug: it previously used the SQLite schema against MySQL). Verify against MySQL after any schema change.
- `create_spend`/`update_spend`/`delete_spend` take an optional `conn` (for reuse in a transaction) and only close it if they opened it.
- Amounts come back as floats; `_serialize` (routes.py) normalizes datetime/string `spent_at` into ISO `date`.
- **MySQL has no `CREATE INDEX IF NOT EXISTS`.** `_ensure_index` checks `information_schema` there and `ALTER TABLE`s. Index creation is wrapped in try/except in `init_db()`: a missing `ALTER` privilege must not crash startup.

## Local dev backend

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export AUTH_STRING=mysecret DB_HOST=127.0.0.1 DB_PORT=3306 \
       DB_USER=savely DB_PASSWORD=... DB_NAME=savely
python -m pytest -q          # run tests (SQLite)
gunicorn --bind 0.0.0.0:5000 wsgi:app
```

To test against the in-cluster DB: `kubectl port-forward svc/mysql 3306:3306 -n savely` then connect.

## Tests

```bash
cd backend && . .venv/bin/activate && python -m pytest -q
```

45 tests in `backend/tests/test_app.py`, all using a SQLite temp-file fixture (no live DB needed). Run before pushing backend changes; CI also runs them in the `test-back` job.

## local dev frontend

```bash
cd frontend && npm install && npm run dev   # dev server proxies /api to localhost:5000
```

## CI/CD

GitHub Actions on push to `master`, **path-filtered**:
- `cicd-back.yml` (`backend/**`): job `test-back` (pytest) → `Deploy back` (build+push `incept1on/savely:back` → `kubectl rollout restart deployment backend -n savely`).
- `cicd-front.yml` (`frontend/**`): build+push `incept1on/savely:front` → `kubectl rollout restart deployment frontend -n savely`.

Repo secrets: `DOCKER_USERNAME=incept1on`, `DOCKER_PASSWORD` (Docker Hub token), `KUBE_CONFIG` (base64 kubeconfig). The `kubectl` calls use `tale/kubectl-action@v1`.

> Note: on the **very first** frontend push the `savely` namespace didn't exist yet, so the rollout step failed with `namespaces "savely" not found` — harmless (ArgoCD hadn't created it yet; the image was still pushed). Namespace now exists, so this won't recur.

## Deploy / infra (overview — manifests live in `~/devops/inceptech`, not this repo)

- **ArgoCD** App `savely` (App-of-Apps), auto-created by `root-app`; sync `prune: true, selfHeal: true`. Manifests in `inceptech/apps/savely/` + wrapper `inceptech/apps/savely.yaml`.
- Resources: `Namespace savely`; `backend` Deployment (2 replicas, svc `backend:5000`); `frontend` Deployment (2 replicas, svc `frontend:80`); `mysql` StatefulSet (1 replica, svc `mysql:3306`, PVC `mysql-data`); `ExternalSecret savely-secret` (SourceReady → `SecretSynced`).
- **Secrets** come from OpenBao (`secret/data/savely`: `AUTH_STRING`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_HOST`, `DB_PORT`) via the `openbao-conf` SecretStore; role `siyuan-role`/policy `siyuan-reader`. Do NOT commit secrets.
- **Edge TLS**: nginx load-balancer (`apps/nginx-load-balancer/`) terminates TLS; cert-manager `regru-dns` ClusterIssuer issued `savely-server-tls` in `nginx-load-balancer` ns, mounted at `/etc/nginx/certs/savely`. Config in `nginx-inceptech-loadblalancer/conf.d/savely-nginx.conf`: `/api/` → `backend.savely.svc.cluster.local:5000/api/`, `/` → `frontend.savely.svc.cluster.local:80`.
- **Backups**: Velero schedule `savely-daily-backup` (daily `0 3 * * *`, TTL 168h, fs-backup) for the `savely` namespace.

---

## Work history (what was done)

### Setup & scaffolding
- Created public GitHub repo `Incept10n/savely` (master), set 3 repo secrets (`DOCKER_USERNAME`, `DOCKER_PASSWORD`, `KUBE_CONFIG`).
- Implemented the full app in one pass:
  - Flask backend: auth (`require_auth`, `X-Auth-String`), `/api/spends` CRUD, `/api/health`, dual SQLite/MySQL schema.
  - 16 pytest tests.
  - React+Vite frontend: login screen + **Spends** tab (quick add, current date) + **History** tab (add with date, edit, delete).
  - Dockerfiles for both, local nginx.conf.
  - CI/CD workflows (`cicd-back.yml`, `cicd-front.yml`) mirroring watchly-3d-models.
- Pushed the `savely` repo, plus the `inceptech` repo (manifest + load-balancer cert/mount) and the `nginx-inceptech-loadblalancer` repo (`savely-nginx.conf`).

### Backend bugfixes (runtime, found via in-cluster e2e)
1. **Wrong schema**: `init_db()` used the SQLite schema against MySQL → table not created. Fixed to select `MYSQL_SCHEMA` when DB type is not SQLite. Tests still passed; CI rebuilt `incept1on/savely:back`; `spends` table verified via `SHOW TABLES`.
2. **Wrong placeholders**: `create_spend`/`update_spend`/`delete_spend` used SQLite-style `?` placeholders, but PyMySQL needs `%s` → `POST /api/spends` returned 500 (`TypeError: not all arguments converted during string formatting`). Fixed by making `_execute()` translate `?` → `%s` for MySQL. Verified against live MySQL (mogrify + insert succeeded), then full e2e passed.

### Currency
- Changed amount display from `$` to **rubles**: `HistoryTab.tsx` now renders `{s.amount.toFixed(2)} ₽`; input labels in `SpendsTab.tsx` and `HistoryTab.tsx` are now `Amount, ₽`. Pushed; front CI passed; `₽` confirmed in the served JS bundle at https://savely.inceptech.ru/.

### Monthly totals (the total never reset)
- **Root cause**: there was no month scoping anywhere. `GET /api/spends` returns the whole table and `SpendsTab` summed all of it, so the total grew forever. Only the AI path was month-scoped (`ai._month_spends`, `%Y-%m` prefix).
- New `frontend/src/months.ts`: `monthKeyOf` (`YYYY-MM` by **local** time — backend sends naive ISO, which browsers parse as local), `currentMonthKey`, `monthLabel` (`en-US`, "October 2026"), `groupByMonth` (sorted desc), `toLocalIso` (local wall time, no offset).
- **Spends tab**: total + `≈ ₽/day` now count the current month only, with the month label above the sum. AI button gates on current-month spends.
- **History tab**: flat list (with edit/delete) shows the current month only, with `Month · total` as a card title. Below it, one collapsible `<details>` block per month, newest first — summary shows month name + that month's total, body lists read-only spends. Native `<details>` keeps the disclosure marker, so no expand state in React.
- **Timezone fix**: quick "Apply" sent no `date`, so the backend stored `datetime.now()` = **pod UTC**; a spend added at 00:30 local on the 1st landed in the previous month. `App.addSpend` now always sends `toLocalIso(new Date())` (or `${date}T12:00:00` for manual dates).
- No backend/API/DB changes. Verified with `tsc --noEmit`, `eslint`, `vite build`, `pytest` (25 passed) and an SSR render check of both tabs.

### AI cost + accuracy rework
- **Problem**: one "Analyze with AI" click on a heavy month cost ~2 ₽. Cost scaled linearly with the *transaction count* in both directions — input prompt lines **and** the `indices` array the model had to emit per row.
- **Prompt**: dates dropped (rows resolve back by index, so a date per spend was pure overhead); amounts kept because the `notice` reasons about which category dominates. `temperature` 0.1 → **0** (classification is a lookup). Output budget 1000 → 300, and `YANDEX_AI_MODEL_MAX_TOKENS` now drives **both** `completionOptions.maxTokens` and the guard estimate — they used to be two independent values.
- **Month scoping moved to SQL**: `db.list_spends_month(start, end)` with bounds from `ai.month_bounds()`; the AI path no longer pulls the whole history table. `GET /api/spends` is untouched on purpose.
- **Fixed 15-item taxonomy** in the system prompt with per-category examples. `normalize_category` maps anything off-list (or decorated, e.g. `Продукты/супермаркет`) onto the list, else `Другое` — this is what stops category names from jumping between months.
- **Per-category `total`/`count`/`share` computed on the server** (`_bucketize`), never by the model. Indices the model omits or points out of range fall into `Другое` instead of vanishing, so buckets always sum to the month total. Rendered in the Spends tab as `Категория · N · сумма · доля %`.
- **Reliability**: retries with exponential backoff on 429/5xx (a single throttled call used to surface as 502 and waste the click); `responseFormat` requested by default with automatic fallback for models that reject it.
- **Measurement**: `/api/ai/analyze` now returns `metrics` (`rows`, `uniqueComments`, estimated/actual tokens and cost) and the Spends tab shows them. `uniqueComments / rows` decides whether grouping identical comments is worth building — the answer is data-dependent, so nothing was built pre-emptively.
- **Rejected on purpose**: keyword rules and fuzzy clustering. Comments are slangy and written in any case; a bespoke normalizer would be brittle and buys only dedup, not accuracy.
- Verified: `pytest` (45 passed), `tsc --noEmit`, `eslint`, `vite build`. Not yet deployed — the metrics line has to be read on real data first.

### Secrets / infra
- OpenBao: created `secret/data/savely` (AUTH_STRING + MySQL creds), updated `siyuan-reader` ACL policy to add `secret/metadata/savely[/...]` (list/read) + `secret/data/savely[/...]` (read); k8s auth role `siyuan-role` binds the `external-secrets` SA to that policy.
- Velero: created schedule `savely-daily-backup`.

### Verification (final state)
- ArgoCD `savely` App: **Synced**, **Healthy**.
- Pods all `1/1 Running`: backend ×2, frontend ×2, `mysql-0`. Rollouts succeed.
- Public e2e via the nginx balancer at https://savely.inceptech.ru/:
  - `/` → 200 (React app)
  - `/api/health` → `{"status":"ok"}`
  - `/api/spends` without auth → 401; with `X-Auth-String` → CRUD all work (POST 201, GET list, PUT, DELETE 204, DELETE-missing 404, date preserved).
  - Front CI's first run "failed" only because the `savely` namespace didn't exist yet (timing); harmless — later runs pass.