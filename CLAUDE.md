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

There is no test suite, linter or build step. Frontend assets are served as-is from `static/`. To verify a change, run the server and exercise it in the browser or with curl. On first start with no Admin user, the server seeds `admin` / `admin123` and a set of default labels.

`run_server.bat` is the production launcher on a Windows host (it runs at boot from a scheduled task and logs to `logs/server.log`). It uses hardcoded Windows paths, so don't "fix" it for Linux.

## Configuration

- The DB URL is hardcoded in `database.py` (`postgresql://…@localhost:5432/gjn_db`). The JWT secret and cookie name are hardcoded in `auth.py`.
- `.env` (gitignored) is loaded by a hand-rolled `_load_dotenv` at the top of `server.py`, not python-dotenv. Real env vars take precedence. It currently holds only the `RS9N_API_BASE` and `RS9N_API_TOKEN` settings for the biometric clock feature.

## Architecture

**Backend: almost everything is in `server.py`.** All routes live there, along with serializer helpers (`serialize_project`, `serialize_sprint`, `serialize_user`), `log_activity`, and `sync_due_notifications`. The due-date notifications are generated lazily when notifications are fetched; there is no scheduler. Import-time side effects in `server.py`: it creates `uploads/` and `backups/`, runs `Base.metadata.create_all`, and runs `upgrade_db.run_upgrade()`.

**Schema changes:** `create_all` only creates missing tables. It never alters existing ones. When you add a column to `models.py`, also add a guarded `ALTER TABLE` in `upgrade_db.py` (it uses `column_exists` / `table_exists` checks and raw PostgreSQL DDL). Otherwise existing databases break.

**Auth / roles (`auth.py`):** a JWT sits in an HTTP-only cookie (`task_management_auth_token`). Routes use `Depends(auth.get_current_user)` or `Depends(auth.check_permission([...roles]))`. There are four roles: Admin, Manager, Developer and Viewer. The role-group constants (`ROLES_BULK`, `ROLES_PROJECT_MANAGE`, …) and the helpers `can_*` and `developer_can_access_task` are the source of truth for authorization. Developers only see their own tasks plus unassigned ones, enforced by `filter_tasks_for_user` in `server.py`. Every new task endpoint must apply this filter or check.

**Task status:** the columns are fixed: Backlog → Todo → In Progress → Review → Testing → Done. Always change status through `apply_task_status()`, which sets and clears `completed_at`. Analytics, burndown and velocity depend on `completed_at`.

**Dates:** `Task.due_date`, `Sprint.start_date` and `Sprint.end_date` are **strings** (`YYYY-MM-DD`), not Date columns. Timestamps are naive UTC (`datetime.utcnow`).

**Biometric clock "time test" (`/api/admin/test-time`, `/test-time` view):** this sets an attendance machine's clock to a target time, waits, then restores it. It runs as a FastAPI `BackgroundTask`, with progress kept in the in-memory `TIME_TEST_LOGS` dict that the frontend polls. There are two backends:
- `machine="zk"`: talks directly to a ZKTeco device through `bio/zk_tool.py` (pyzk).
- `machine="rs9n"`: goes over HTTP to a separate attendance API service (`rs9n_call`), because the RS9N is a push device and can't be dialled directly.

`bio/` also contains standalone ZKTeco tools (see `bio/README.md`).

**Frontend (`static/`):**
- `index.html` is the SPA shell **and** contains the main app logic in one large inline `<script>`: state, API calls, view rendering and routing via `VIEW_PATHS` / `history.pushState`.
- Feature layers load after that script and hook into it: `js/upgrade.js` (Phase 1: projects, bulk ops, subtasks), `js/phase2.js` (Phase 2: sprints, skills, notifications, charts) and `js/layout.js` (responsive shell, mobile Kanban).
- CSS goes through `css/app.css`, which `@import`s the split files. Don't add inline `<style>` to `index.html`.
- CDN deps: Chart.js, marked and lucide.
- **Adding a new client-side route:** add it to `VIEW_PATHS` in `index.html`, and also add a matching `@app.get` route in `server.py` that returns `_spa_index()`. A catch-all fallback exists, but the explicit routes are the convention.
- `service-worker.js` caches `/` and `app.css` under `CACHE_NAME`. Bump the version when cached assets change, or clients will see stale files.

**Files and backups:** attachments go to `uploads/`, served at `/uploads`. The Admin backups endpoints read and write `backups/`. Both directories are gitignored.

`scripts/` holds one-off refactoring helpers from the CSS split. They are not part of the runtime.
