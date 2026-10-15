# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Task_itms is an internal task/sprint management app: a **FastAPI** backend (README.md wrongly says Flask) serving a vanilla-JS single-page app, backed by PostgreSQL. `.cursor/rules/codebase.md` has more detail on roles, routes and CSS layout. Keep it in sync when those change.

## Commands

```bash
pip install -r requirements.txt        # plus `pip install pyzk` — server.py imports bio/zk_tool.py
python server.py                       # uvicorn on 127.0.0.1:5001, no reload
uvicorn server:app --host 0.0.0.0 --port 5001 --reload   # dev alternative
python upgrade_db.py                   # idempotent schema upgrades (also runs on every server import)
python migrate.py                      # one-off: copy legacy SQLite database.db -> PostgreSQL
```

There is no test suite, linter or build step. Frontend assets are served as-is from `static/`. To verify a change, run the server and exercise it in the browser or with curl. Never test against `gjn_db`. Use a throwaway database in the same container (`docker exec postgres18 psql -U admin -d postgres -c "CREATE DATABASE gjn_db_test"`) and run a copy of the app with `DATABASE_URL` pointing at it. Only on an **empty** users table does startup create `admin`, with `INITIAL_ADMIN_PASSWORD` or a random password printed once to the log. It also seeds default labels.

`run_server.bat` is the production launcher on a Windows host (it runs at boot from a scheduled task and logs to `logs/server.log`). It uses hardcoded Windows paths, so don't "fix" it for Linux.

## Configuration

- All settings come from the environment, through `config.py`. It loads `.env` (gitignored, mode 600) with a hand-rolled parser, not python-dotenv, and real env vars take precedence. `config.py` is imported first by `server.py`, `database.py` and `auth.py`, so standalone scripts such as `upgrade_db.py` get `.env` too. `.env.example` lists every key.
- `DATABASE_URL` and `JWT_SECRET_KEY` are required; startup fails without them. Changing `JWT_SECRET_KEY` logs everyone out. Backups take the pg_dump credentials from `DATABASE_URL`, and the container from `BACKUP_PG_CONTAINER`. Never put secrets back into the source: the old ones are in the git history.

## Architecture

**Backend: almost everything is in `server.py`.** All routes live there, along with serializer helpers (`serialize_project`, `serialize_sprint`, `serialize_user`), `log_activity`, and `sync_due_notifications`. The due-date notifications are generated lazily when notifications are fetched; there is no scheduler. Import-time side effects in `server.py`: it creates `uploads/` and `backups/`, runs `Base.metadata.create_all`, and runs `upgrade_db.run_upgrade()`.

**Schema changes:** `create_all` only creates missing tables. It never alters existing ones. When you add a column to `models.py`, also add a guarded `ALTER TABLE` in `upgrade_db.py` (it uses `column_exists` / `table_exists` checks and raw PostgreSQL DDL). Otherwise existing databases break.

**Auth / roles (`auth.py`):** a JWT sits in an HTTP-only, SameSite=Lax cookie (`task_management_auth_token`), marked Secure when the request came in over HTTPS through cloudflared. The token holds `sub` (the user **id**) and `ver`, which must equal `users.token_version`. `auth.revoke_sessions(user)` bumps the version, and is called on password and role changes. Login is throttled in memory (5 failures per 15 min, keyed by IP and by username). Client IP is read from `CF-Connecting-IP` when the peer is loopback. You can't remove or delete the last Admin.

**Request hardening (`security_middleware` in `server.py`):** sets CSP and other security headers, and adds `no-store` on `/api/*`. It refuses `/api` POST, PUT, PATCH and DELETE requests whose `Origin` names a different host. There is no CORS on purpose, because the SPA is same-origin. If you add a new CDN, extend the CSP.

**Validation:** request models in `schemas.py` use `Literal` enums (role, status, priority, category, project and sprint status) and constrained strings and dates. Response models stay plain `str`. `check_refs()` turns unknown assignee, project, sprint, label or lead ids into a 400.

**Roles:** Routes use `Depends(auth.get_current_user)` or `Depends(auth.check_permission([...roles]))`. There are four roles: Admin, Manager, Developer and Viewer. The role-group constants (`ROLES_BULK`, `ROLES_PROJECT_MANAGE`, …) and the helpers `can_*` and `developer_can_access_task` are the source of truth for authorization. Developers only see their own tasks plus unassigned ones, enforced by `filter_tasks_for_user` in `server.py`. Every new task endpoint must apply this filter or check.

**Task status:** the columns are fixed: Backlog → Todo → In Progress → Review → Testing → Done. Always change status through `apply_task_status()`, which sets and clears `completed_at`. Analytics, burndown and velocity depend on `completed_at`.

**Dates:** `Task.due_date`, `Sprint.start_date` and `Sprint.end_date` are **strings** (`YYYY-MM-DD`), not Date columns. Timestamps (`created_at`, `completed_at`, `ActivityLog.timestamp`, …) are naive UTC (`datetime.utcnow`) and serialize **without** a timezone suffix. The frontend must parse them as `new Date(value + 'Z')`; without the `Z`, IST users see times 5½ h off. Server-side "today" and "this week" checks use local `datetime.now()`, so mixing them with UTC timestamps is a known source of off-by-one-day bugs.

**Update semantics:** the `PUT` handlers (tasks, projects, sprints, users) copy only fields that are not `None`. Sending `null` therefore can't clear a field such as assignee, due date, project or sprint. Fixing that needs `model_dump(exclude_unset=True)`, not a `None` check.

**Biometric clock "time test" (`/api/admin/test-time`, `/test-time` view):** this sets an attendance machine's clock to a target time, waits, then restores it. It runs as a FastAPI `BackgroundTask`, with progress kept in the in-memory `TIME_TEST_JOBS` dict. The frontend polls `/logs/{task_id}` until the job reports `done`. There are two backends, registered in `TIME_TEST_MACHINES`:
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
  - `refreshDataSilently`, the 60 s auto-refresh, is not patched at all.
- **Escaping is mandatory.** Rendering builds `innerHTML` from template strings, so every server-supplied value must be wrapped with the helpers at the top of the inline script:
  - `esc()` for text and attribute values.
  - `jsArg()` for values inside inline `onclick="fn(...)"` (HTML escaping alone is undone by the browser before the JS runs).
  - `safeColor()` for colours in style attributes.
  - `renderMarkdown()` (marked + DOMPurify, pinned with SRI) for Markdown.

  Use `textContent` wherever no HTML is needed.
- CSS goes through `css/app.css`, which `@import`s the split files. Don't add inline `<style>` to `index.html`. Theme colours are `--success`, `--warning`, `--error` and `--accent-color` (from `tokens.css`). The `--success-color` / `--danger-color` / `--primary-color` names used in older code are undefined.
- CDN deps: Chart.js, marked and lucide. All are unpinned (`@latest`) and have no SRI.
- **Adding a new client-side route:** add it to `VIEW_PATHS` in `index.html`, and also add a matching `@app.get` route in `server.py` that returns `_spa_index()`. A catch-all fallback exists, but the explicit routes are the convention.
- `service-worker.js` serves every `/static/*` request **cache-first, permanently**. Page navigations and `/api/*` always go to the network. **Bump `CACHE_NAME` whenever any file under `static/` changes**, or clients keep running old JS/CSS against new HTML.

**Files and backups:** attachments are saved to `uploads/` under random `uuid4` names, limited by `MAX_UPLOAD_MB` (default 25). The original filename is kept only in the DB. Files are served **only** by `GET /api/attachments/{id}`, which checks auth and roles and always sends a download with octet-stream. `uploads/` is deliberately not mounted. Backup filenames must match `BACKUP_NAME_RE`. `POST /api/backups` runs `pg_dump` via `docker exec postgres18`, and the Admin backup endpoints read and write `backups/`. Both directories are gitignored.

`scripts/` holds one-off refactoring helpers from the CSS split. They are not part of the runtime.
