# Task_itms — Codebase Knowledge

## Stack
- **Backend**: FastAPI (`server.py`), SQLAlchemy ORM (`models.py`), Pydantic (`schemas.py`)
- **Auth**: JWT in HTTP-only cookie (`auth.py`), bcrypt passwords
- **Database**: PostgreSQL (`postgresql://admin:gajendra@localhost:5432/gjn_db`)
- **Frontend**: `static/index.html` + `static/js/layout.js` + `static/js/upgrade.js` + `static/js/phase2.js` + CSS via `static/css/app.css`
- **Run**: `run_server.bat` or `uvicorn server:app --host 0.0.0.0 --port 5001`

## Roles (4-tier)
| Role | Capabilities |
|------|-------------|
| **Admin** | Users, backups, labels delete, project/sprint delete, bulk ops, all tasks |
| **Manager** | Projects/sprints CRUD (not delete), bulk ops, all tasks, task delete |
| **Developer** | Own + unassigned tasks only; cannot bulk-assign or reassign to others |
| **Viewer** | Read-only; kanban hidden, table list instead |

## Core entities
- `User` — includes `skills` (comma-separated), exposed as `skill_list` in API
- `Task` — `project_id`, `sprint_id`, `completed_at` (set when status → Done)
- `Project`, `Sprint`, `Subtask`, `Label`, `Comment`, `Attachment`, `ActivityLog`, `Notification`

## Task workflow columns (fixed)
Backlog → Todo → In Progress → Review → Testing → Done

## Key API routes
- `/api/tasks`, `/api/tasks/bulk` (assign/status/priority/project/sprint/delete)
- `/api/projects`, `/api/sprints`, `/api/sprints/{id}/burndown`
- `/api/notifications`, `/api/notifications/unread-count`
- `/api/users?skill=Backend` — filter by skill
- `/api/analytics` — includes `completed_this_week`, `developer_completion`, `sprint_velocity`

## Migrations
- `upgrade_db.py` — idempotent schema upgrades (run on server start)

## Frontend structure
- **Entry**: `static/index.html` (SPA shell, no inline CSS)
- **CSS**: `static/css/app.css` imports:
  - `tokens.css` — design tokens
  - `components.css` — sidebar, forms, modals, shared UI
  - `views.css` — dashboard, calendar, table views
  - `layout.css` — mobile topbar, drawer, collapsible sidebar, kanban toolbar
  - `kanban.css` — horizontal scroll board (300px columns), mobile status tabs, card polish
  - `upgrade.css` / `phase2.css` — Phase 1–2 feature styles
  - `responsive.css` — breakpoints (1024 / 768 / 480)
- **JS**: `layout.js` (responsive shell + Kanban mobile), `upgrade.js` (Phase 1), `phase2.js` (Phase 2)

## Frontend navigation
- Client-side routes update the browser URL via `history.pushState`
- Reloading a deep link works because the server serves `index.html` for each app route
- **Routes**: `/` (Kanban), `/all-tasks`, `/dashboard`, `/calendar`, `/projects`, `/sprints`, `/users`, `/backups`
- **Desktop**: 6 fixed-width columns (300px), horizontal scroll with edge shadows
- **Mobile (≤768px)**: status tabs show one column at a time; touch “move” menu on cards
- **Sidebar**: collapsible on desktop; drawer + backdrop on mobile

## Phase history
- **Phase 1**: Projects, bulk table ops, subtasks, Manager role
- **Phase 2**: Sprints + burndown/velocity, developer skills, in-app due-date alerts, completion-rate chart
- **UI upgrade**: CSS split, responsive Kanban, mobile drawer, collapsible filters
