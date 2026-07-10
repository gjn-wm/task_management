# Task_itms — Codebase Knowledge

## Stack
- **Backend**: FastAPI (`server.py`), SQLAlchemy ORM (`models.py`), Pydantic (`schemas.py`)
- **Auth**: JWT in HTTP-only cookie (`auth.py`), bcrypt passwords
- **Database**: PostgreSQL (`postgresql://admin:gajendra@localhost:5432/gjn_db`)
- **Frontend**: Single-page app in `static/index.html` + `static/js/upgrade.js` + `static/css/upgrade.css`
- **Run**: `run_server.bat` or `uvicorn server:app --host 0.0.0.0 --port 5001`

## Roles (4-tier)
| Role | Capabilities |
|------|-------------|
| **Admin** | Users, backups, labels delete, project delete, bulk ops, all tasks |
| **Manager** | Projects CRUD (not delete), bulk ops, all tasks, task delete |
| **Developer** | Own + unassigned tasks only; cannot bulk-assign or reassign to others |
| **Viewer** | Read-only; kanban hidden, table list instead |

## Core entities
- `User`, `Task`, `Label` (many-to-many via `task_labels`)
- `Project` — name, description, status (Active/On Hold/Closed), optional lead
- `Subtask` — checklist items per task
- `Comment`, `Attachment`, `ActivityLog`

## Task workflow columns (fixed)
Backlog → Todo → In Progress → Review → Testing → Done

## Key API routes
- `/api/tasks`, `/api/tasks/bulk` (Admin/Manager)
- `/api/projects` (list: all; write: Admin/Manager; delete: Admin)
- `/api/tasks/{id}/subtasks`, `/api/subtasks/{id}`
- `/api/analytics?project_id=`

## Migrations
- `upgrade_db.py` — idempotent schema upgrades (run on server start)
- `migrate.py` — one-time SQLite → PostgreSQL data migration

## Phase 1 upgrade (Jul 2026)
Projects, bulk table ops, subtasks/checklists, Manager role. No client/CRM layer — internal projects only.
