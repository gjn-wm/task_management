# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Task_itms is an internal task/sprint management app: a **FastAPI** backend (README.md wrongly says Flask) serving a vanilla-JS single-page app, backed by PostgreSQL. `.cursor/rules/codebase.md` has more detail on roles, routes and CSS layout. Keep it in sync when those change.

## Commands

```bash
pip install -r requirements.txt        # pinned; plus `pip install pyzk` for the ZK time test
pip install -r requirements-dev.txt    # pytest + httpx2 (TestClient)
python server.py                       # uvicorn on 127.0.0.1:5001, no reload
uvicorn server:app --host 0.0.0.0 --port 5001 --reload   # dev alternative
python upgrade_db.py                   # idempotent schema upgrades (also runs when server.py is imported)
python migrate.py                      # one-off: copy legacy SQLite database.db -> PostgreSQL

# tests: TEST_DATABASE_URL is required and its database name must end in "_test"
TEST_DATABASE_URL=postgresql://admin:<pw>@localhost:5432/gjn_db_test python -m pytest -q
python -m pytest tests/test_tasks.py::test_name -q     # a single test
python scripts/check_frontend.py       # node --check on every JS file and the inline script
```

**Tests (`tests/`)** run the real app through FastAPI's `TestClient` against a real PostgreSQL database. `conftest.py` creates the `_test` database if it is missing, truncates all tables before each test, points `UPLOAD_DIR`/`BACKUP_DIR` at temp dirs and lowers the bcrypt cost. Drop the test database afterwards (`DROP DATABASE gjn_db_test`). `test_query_counts.py` fails if an endpoint's SQL statement count grows with the number of rows; when it fails, eager-load the relationship instead of loosening the test. The time-test tests replace `rs9n_call` with a fake, so no device is contacted. CI (`.github/workflows/ci.yml`) runs compileall, `check_frontend.py` and pytest against a `postgres:16` service on pushes to `main` and on PRs. There is no linter or build step: frontend assets are served from `static/`.

For manual checks, never use `gjn_db`. Use a throwaway database in the same container (`docker exec postgres18 psql -U admin -d postgres -c "CREATE DATABASE gjn_db_test"`) and run a copy of the app with `DATABASE_URL` pointing at it. Only on an **empty** users table does startup create `admin`, with `INITIAL_ADMIN_PASSWORD` or a random password printed once to the log. It also seeds default labels.

`run_server.bat` is the production launcher on a Windows host (it runs at boot from a scheduled task and logs to `logs/server.log`). It uses hardcoded Windows paths, so don't "fix" it for Linux.

## Configuration

- All settings come from the environment, through `config.py`. It loads `.env` (gitignored, mode 600) with a hand-rolled parser, not python-dotenv, and real env vars take precedence. `config.py` is imported first by `server.py`, `database.py` and `auth.py`, so standalone scripts such as `upgrade_db.py` get `.env` too. `.env.example` lists every key.
- `DATABASE_URL` and `JWT_SECRET_KEY` are required; startup fails without them. Changing `JWT_SECRET_KEY` logs everyone out. Backups take the pg_dump credentials from `DATABASE_URL`, and the container from `BACKUP_PG_CONTAINER`. Never put secrets back into the source: the old ones are in the git history.

## Architecture

**Backend: almost everything is in `server.py`.** All routes live there, along with serializer helpers (`serialize_project`, `serialize_sprint`, `serialize_user`), `log_activity`, and `sync_due_notifications`. The due-date notifications are generated lazily when notifications are fetched; there is no scheduler. Import-time side effects in `server.py`: it creates `uploads/` and `backups/`, runs `Base.metadata.create_all`, and runs `upgrade_db.run_upgrade()`. Startup work (`create_initial_admin`, `recover_interrupted_time_test`) runs in the `lifespan` handler, not `@app.on_event`. `python server.py` passes the `app` object to uvicorn (not the `"server:app"` string), so the module and the upgrade run once per start. `STATIC_DIR`, `UPLOAD_DIR` and `BACKUP_DIR` are absolute paths; the last two can be overridden by env vars of the same name.

**Query performance:** never let a list endpoint lazy-load per row.
- Task lists go through `build_task_query()`, which applies `TASK_LIST_LOAD` (`selectinload` for assignee, labels, subtasks, project, sprint). `get_task` also eager-loads comments and attachments with their users. Any relationship a serializer touches must be in the load set.
- Project and sprint task counts come from one aggregate query (`sprint_stats_map`; a grouped count for projects) passed into `serialize_project`/`serialize_sprint`, not from `len(obj.tasks)`.
- `GET /api/tasks` returns the full (role-filtered) list for Kanban and calendar. The All Tasks table uses `GET /api/tasks/page` (`limit` 1–200, `offset`, `open_only`, `overdue`; returns `{items, total, limit, offset}`). It is declared before `/api/tasks/{task_id}`, so keep it there.
- `sync_due_notifications` is throttled per user (`DUE_SYNC_INTERVAL_SEC`), filters `due_date <= tomorrow` in SQL, and loads existing alerts in one query.
- Indexes: `models.INDEXED_COLUMNS` lists the `ix_<table>_<column>` indexes, and `upgrade_db.py` creates them with `CREATE INDEX IF NOT EXISTS`. Add new filter or FK columns there.

**Schema changes:** `create_all` only creates missing tables. It never alters existing ones. When you add a column to `models.py`, also add a guarded `ALTER TABLE` in `upgrade_db.py` (it uses `column_exists` / `table_exists` checks and raw PostgreSQL DDL). Otherwise existing databases break.

**Auth / roles (`auth.py`):** a JWT sits in an HTTP-only, SameSite=Lax cookie (`task_management_auth_token`), marked Secure when the request came in over HTTPS through cloudflared. The token holds `sub` (the user **id**) and `ver`, which must equal `users.token_version`. `auth.revoke_sessions(user)` bumps the version, and is called on password and role changes. Login is throttled in memory (5 failures per 15 min, keyed by IP and by username). Client IP is read from `CF-Connecting-IP` when the peer is loopback. You can't remove or delete the last Admin.

**Request hardening (`security_middleware` in `server.py`):** sets CSP and other security headers, and adds `no-store` on `/api/*`. It refuses `/api` POST, PUT, PATCH and DELETE requests whose `Origin` names a different host. There is no CORS on purpose, because the SPA is same-origin. If you add a new CDN, extend the CSP.

**Validation:** request models in `schemas.py` use `Literal` enums (role, status, priority, category, project and sprint status) and constrained strings and dates. Response models stay plain `str`. `check_refs()` turns unknown assignee, project, sprint, label or lead ids into a 400.

**Roles:** Routes use `Depends(auth.get_current_user)` or `Depends(auth.check_permission([...roles]))`. There are four roles: Admin, Manager, Developer and Viewer. The role-group constants (`ROLES_BULK`, `ROLES_PROJECT_MANAGE`, …) and the helpers `can_*` and `developer_can_access_task` are the source of truth for authorization. Developers only see their own tasks plus unassigned ones, enforced by `filter_tasks_for_user` in `server.py`. Every new task endpoint must apply this filter or check.

**Task status:** the columns are fixed: Backlog → Todo → In Progress → Review → Testing → Done. Always change status through `apply_task_status()`, which sets and clears `completed_at`. Analytics, burndown and velocity depend on `completed_at`.

**Dates:** `Task.due_date`, `Sprint.start_date` and `Sprint.end_date` are **strings** (`YYYY-MM-DD`), not Date columns. Timestamps (`created_at`, `completed_at`, `ActivityLog.timestamp`, …) are naive UTC (`models.utcnow()`; don't use the deprecated `datetime.utcnow`) and serialize **without** a timezone suffix. The frontend must parse them with `parseServerDate()`, which appends the `Z`; without it, IST users see times 5½ h off. For a local `YYYY-MM-DD` use `localDateStr()`, never `toISOString()`, which gives the UTC date. On the server, convert a timestamp with `utc_to_local_date()` before comparing it with local "today" or "this week" values.

**Update semantics:** the `PUT` handlers for tasks, projects and sprints use `apply_updates()` (`model_dump(exclude_unset=True)`):
- a field that is omitted is left unchanged
- an explicit `null` clears the fields listed as `nullable` (assignee, due date, project, sprint, project lead)
- a `null` for a required field is ignored

Status still goes through `apply_task_status()`. In the bulk dropdowns, `""` is the "choose…" placeholder and `"none"` means clear.

**Deletes keep history:** comments, attachments and activity entries use `ON DELETE SET NULL` for their author, and activity entries also do for their task. The migration is `set_fk_on_delete_null` in `upgrade_db.py`. The API returns `user: null` for a deleted author, which the UI shows as "Deleted user" via `userLabel()`. Deleting a task removes its attachment files from disk with `remove_attachment_files`.

**Biometric clock "time test" (`/api/admin/test-time`, `/test-time` view):** this sets an attendance machine's clock to a target time, waits, then restores it. It runs as a FastAPI `BackgroundTask`, with progress kept in the in-memory `TIME_TEST_JOBS` dict. The frontend polls `/logs/{task_id}` until the job reports `done`. There are two backends, registered in `TIME_TEST_MACHINES`:
- Admin-only (API and UI).
- `machine="zk"`: talks directly to a ZKTeco device through `bio/zk_tool.py` (pyzk).
- `machine="rs9n"`: goes over HTTP to a separate attendance API service (`rs9n_call`), because the RS9N is a push device and can't be dialled directly. Commands are queued and applied when the device next polls, within a few seconds. `RS9N_API_BASE` in `.env` points at the LAN address (`http://192.168.29.101:5004`), not `rs9n.itmsu.com`. From this network, one of that hostname's Cloudflare IPs intermittently drops connections, which used to stall requests for about 2 minutes. The service runs on a Windows host (`ssh wmwm`, PowerShell) as the scheduled task `\Attendance API 5004`, from `C:\xampp new\htdocs\python\biometric-attendance-api`. Its code is identical to `~/dev_gjn/zkteco_api/biometric-attendance-api`.

The failsafes in that section must be kept:
- Restore always targets the *correct* time, never "original + elapsed". For the RS9N this means `/api/set-time` is sent **with no `time`**, so the service stamps its clock when the device collects the command.
- Once the clock may have been touched, every exit path restores. That includes a set that failed or wasn't confirmed, because an unconfirmed RS9N command stays queued.
- Restores are verified and retried.
- `_time_test_lock` allows only one test or restore at a time.
- `.time_test_state.json` marks an unfinished test. `recover_interrupted_time_test` restores it on startup, and new tests are refused while it exists.
- `POST /api/admin/test-time/restore` is the manual "Sync clock now".

On the Linux Mint host the app runs as the systemd user service `task-itms.service`, published through cloudflared. Restart it with `systemctl --user restart task-itms.service`. Logs are in `journalctl --user -u task-itms.service`.

`bio/` also contains standalone ZKTeco tools (see `bio/README.md`).

**Frontend (`static/`):**
- `index.html` is the SPA shell **and** contains the main app logic in one large inline `<script>`: state, API calls, view rendering and routing via `VIEW_PATHS` / `history.pushState`.
- Feature layers load after that script: `js/upgrade.js` (Phase 1: projects, bulk ops, subtasks), `js/phase2.js` (Phase 2: sprints, skills, notifications, charts) and `js/layout.js` (responsive shell, mobile Kanban). They work by **reassigning the inline script's global functions** on `DOMContentLoaded` (for example `loadTasks = async function() {…}` in `upgrade.js`), often wrapping the original. The inline version is therefore often not the code that runs. Before editing, grep all four files for the function name.
  - `handleSaveTask`, `loadTasks` and `loadAllTasks` are fully replaced in `upgrade.js`.
  - `renderKanbanBoard` and `switchView` are wrapped in more than one layer.
  - `refreshDataSilently` (every 60 s, and when the tab becomes visible) calls the real loaders, so filters survive: `loadTasks`, `loadAllTasks(1, {silent: true})`, `loadCalendar`, and the panel loaders. It skips hidden tabs and in-progress drags.
  - All Tasks is **server-paginated**: `loadAllTasks(page, {quick, silent, paging})` fetches one page from `/api/tasks/page`, and `allTasksTotal` holds the total. `quick` is `'pending'` or `'overdue'` (from the dashboard KPI cards) and maps to `open_only`/`overdue`. `silent` keeps the page and selection; paging keeps the selection; a filter change clears it.
  - The calendar has its own `calendarTasks`, loaded by `loadCalendar()`. It does not share the Kanban list.
- **Escaping is mandatory.** Rendering builds `innerHTML` from template strings, so every server-supplied value must be wrapped with the helpers at the top of the inline script:
  - `esc()` for text and attribute values.
  - `jsArg()` for values inside inline `onclick="fn(...)"` (HTML escaping alone is undone by the browser before the JS runs).
  - `safeColor()` for colours in style attributes.
  - `renderMarkdown()` (marked + DOMPurify, pinned with SRI) for Markdown.

  Use `textContent` wherever no HTML is needed.
- CSS goes through `css/app.css`, which `@import`s the split files. Don't add inline `<style>` to `index.html`. Theme colours are `--success`, `--warning`, `--error` and `--accent-color` (from `tokens.css`). The `--success-color` / `--danger-color` / `--primary-color` names used in older code are undefined.
- CDN deps: Chart.js, marked, lucide and DOMPurify, pinned to exact versions with SRI `integrity` hashes. To upgrade one, change the URL in `index.html` **and** `CDN_ASSETS` in `service-worker.js`, and recompute the hash.
- **Adding a new client-side route:** add it to `VIEW_PATHS` in `index.html`, and also add a matching `@app.get` route in `server.py` that returns `_spa_index()`. A catch-all fallback exists, but the explicit routes are the convention.
- **Caching and deploys.** A changed file reaches users on their next page load; nothing needs bumping by hand.
  - `_spa_index()` serves `index.html` with every local script/stylesheet URL and the service-worker registration rewritten to `...?v=<content hash>`. `app_css()` does the same for the `@import`s in `app.css`, whose own version covers all the partials. A changed file therefore has a URL that no browser, Cloudflare edge or service worker has cached. New local `<script>`/`<link>` tags must use absolute `/static/js/…` or `/static/css/…` paths so the rewrite picks them up.
  - `security_middleware` sends `Cache-Control: no-cache` for pages and static files, plus `CDN-Cache-Control: no-store` for Cloudflare, which otherwise edge-caches `.js`/`.css` for hours whatever the origin says.
  - `service-worker.js` is served from `/service-worker.js` with scope `/` (the old `/static/`-scoped worker never controlled any page and is unregistered by `index.html`). It is **network-first** for navigations and `/static/*` (the cache is only an offline fallback, and older `?v=` copies are pruned), **cache-first** for the pinned CDN files, and leaves `/api/*` and `/uploads/*` alone. Bump `CACHE_NAME` only when you change the worker's caching scheme.

**Files and backups:** attachments are saved to `UPLOAD_DIR` (default `uploads/`) under random `uuid4` names (the DB stores just that name; old `uploads/<name>` values still resolve through `attachment_disk_path()`), limited by `MAX_UPLOAD_MB` (default 25). The original filename is kept only in the DB. Files are served **only** by `GET /api/attachments/{id}`, which checks auth and roles and always sends a download with octet-stream. `uploads/` is deliberately not mounted. Backup filenames must match `BACKUP_NAME_RE`. `POST /api/backups` runs `pg_dump` via `docker exec postgres18`, and the Admin backup endpoints read and write `backups/`. Both directories are gitignored.

`scripts/` holds one-off refactoring helpers from the CSS split, plus `check_frontend.py` (used by CI). None of it is part of the runtime.
