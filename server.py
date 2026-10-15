import os
import shutil
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Response, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import re
import secrets
from urllib.parse import urlparse
from sqlalchemy.orm import Session

import time
import uuid
import json
import urllib.request
import urllib.error
import threading
import traceback
from bio.zk_tool import Managed, op_test_voice

import config  # loads .env before anything reads the environment

# --- Realtime RS9N / S362 biometric machine -------------------------------
# The RS9N cannot be dialled directly the way a ZKTeco can: it is a PUSH device
# that only ever talks to whichever server owns its /fk endpoint. So this app
# reaches it through the attendance API over HTTP — that service owns the device
# connection and queues the time change for the device to collect on its next
# poll (a few seconds). It must be running for machine="rs9n" to work.
#
# Configure both in .env (gitignored):
#   RS9N_API_BASE=http://127.0.0.1:5004
#   RS9N_API_TOKEN=<the API_SECRET_TOKEN of the attendance API>
RS9N_API_BASE = os.getenv("RS9N_API_BASE", "http://127.0.0.1:5004").rstrip("/")
RS9N_API_TOKEN = os.getenv("RS9N_API_TOKEN", "")


def rs9n_call(method: str, path: str, payload: dict = None, timeout: int = 120):
    """Call the attendance API. Raises RuntimeError with a readable message.

    Uses urllib rather than requests/httpx so this adds no new dependency.
    Blocking is fine: every caller here is already a background task.
    """
    # Every RS9N endpoint is bearer-protected. Say so plainly rather than letting
    # it surface as an opaque 401 from the service.
    if not RS9N_API_TOKEN:
        raise RuntimeError(
            "RS9N_API_TOKEN is not configured. Add it to the .env file next to "
            "server.py (it must match the API_SECRET_TOKEN of the attendance API), "
            "then restart this server."
        )

    url = RS9N_API_BASE + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + RS9N_API_TOKEN)
    # Cloudflare in front of the attendance API rejects urllib's default
    # "Python-urllib/x.y" User-Agent with a 403 (error 1010) before the request
    # ever reaches the service, so identify ourselves explicitly.
    req.add_header("User-Agent", "Task_itms/1.0 (+rs9n-time-test)")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise RuntimeError(
                f"The attendance API at {RS9N_API_BASE} sent a non-JSON reply: {raw[:120]!r}")
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        # Cloudflare blocks come back as a plain-text "error code: NNNN" body
        # with Server: cloudflare. Don't misreport those as a bad token.
        if (e.headers.get("Server", "").lower() == "cloudflare"
                and err_body.strip().startswith("error code:")):
            raise RuntimeError(
                f"Cloudflare blocked the request to the attendance API at "
                f"{RS9N_API_BASE} (HTTP {e.code}, {err_body.strip()}). "
                "The request never reached the service; check the Cloudflare "
                "firewall / Browser Integrity Check rules for this server.")
        # 520-527: Cloudflare could not get an answer from the service in time
        # (524 = it gave up after 100s). The request may well have been handled.
        if 520 <= e.code <= 527:
            raise RuntimeError(
                f"Cloudflare got no timely answer from the attendance API (HTTP {e.code}). "
                "The request may still have been processed.")
        # The attendance API returns a JSON envelope with a useful message —
        # top-level, or under "detail" for its HTTPExceptions — so surface that
        # rather than a bare "HTTP 503".
        detail = ""
        try:
            body = json.loads(err_body)
            nested = body.get("detail")
            detail = body.get("message") or (
                nested.get("message") if isinstance(nested, dict) else nested) or ""
        except Exception:
            pass
        if e.code in (401, 403):
            raise RuntimeError(
                "The attendance API rejected our token (HTTP %d). RS9N_API_TOKEN "
                "in .env must match the API_SECRET_TOKEN that service runs with."
                % e.code)
        raise RuntimeError(detail or f"Attendance API returned HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach the attendance API at {RS9N_API_BASE} ({e.reason}). "
            "Start attendance_api.py, or set the RS9N_API_BASE environment variable."
        )
    except (TimeoutError, OSError) as e:
        # Raised mid-response (read timeout, dropped connection), so it is not
        # wrapped in URLError. The service may have acted on the request anyway.
        raise RuntimeError(
            f"The attendance API at {RS9N_API_BASE} did not answer in time or dropped "
            f"the connection ({e or type(e).__name__}). The request may still have been "
            "processed.")

import models
import schemas
import auth
from database import engine, get_db, Base, DATABASE_URL
from upgrade_db import run_upgrade

# Create directories
os.makedirs("uploads", exist_ok=True)
os.makedirs("backups", exist_ok=True)

# Initialize database
Base.metadata.create_all(bind=engine)
run_upgrade()

app = FastAPI(
    title="Task Management API",    
    docs_url=None,       # Disables /docs
    redoc_url=None,      # Disables /redoc
    openapi_url=None 
    )
# The SPA is served from this same origin, so no CORS is needed. (It used to
# allow every origin *with* credentials, letting any website call the API as
# the logged-in user wherever the browser sent the cookie.)

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    # 'unsafe-inline' is still needed for the inline app script and onclick
    # handlers; the rest of the policy still blocks foreign scripts and framing.
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "connect-src 'self' https://cdn.jsdelivr.net",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # CSRF backstop on top of SameSite=Lax: a browser always sends Origin on
    # cross-site state-changing requests, so refuse any that names another host.
    if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and origin != "null":
            host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
            if urlparse(origin).netloc.lower() != host.lower():
                return JSONResponse(status_code=403, content={"detail": "Cross-site request refused."})

    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response

# Startup event to create default admin user if none exists
@app.on_event("startup")
def create_initial_admin():
    db = next(get_db())
    try:
        # Only on a brand-new install. It used to recreate admin/admin123 whenever
        # no Admin existed, e.g. after the last Admin was demoted.
        if db.query(models.User).count() == 0:
            password = os.environ.get("INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
            admin_user = models.User(
                username="admin",
                password_hash=auth.hash_password(password),
                role="Admin",
                full_name="System Administrator"
            )
            db.add(admin_user)
            db.commit()
            if os.environ.get("INITIAL_ADMIN_PASSWORD"):
                print("Initial admin user created: admin (password from INITIAL_ADMIN_PASSWORD)", flush=True)
            else:
                print(f"Initial admin user created: admin / {password}  (change it after logging in)", flush=True)
            
        # Create some default labels if they don't exist
        default_labels = [
            ("AI", "#8b5cf6"),
            ("YOLO", "#d946ef"),
            ("ANPR", "#ec4899"),
            ("CCTV", "#f43f5e"),
            ("Dashboard", "#3b82f6"),
            ("Backend", "#10b981"),
            ("Frontend", "#06b6d4"),
            ("API", "#14b8a6"),
            ("Database", "#f59e0b"),
            ("Testing", "#eab308"),
            ("Bug", "#ef4444"),
            ("Urgent", "#dc2626")
        ]
        for name, color in default_labels:
            exists = db.query(models.Label).filter(models.Label.name == name).first()
            if not exists:
                label = models.Label(name=name, color=color)
                db.add(label)
        db.commit()
    except Exception as e:
        print(f"Startup error: {e}")
    finally:
        db.close()

@app.api_route("/health", methods=["GET", "HEAD"])
def health_check():
    return {"status": "ok"}

# Log activities helper
def log_activity(db: Session, user_id: int, action: str, details: str, task_id: Optional[int] = None):
    log = models.ActivityLog(
        user_id=user_id,
        task_id=task_id,
        action=action,
        details=details
    )
    db.add(log)
    db.commit()

def project_task_count(db: Session, project_id: int) -> int:
    return db.query(models.Task).filter(models.Task.project_id == project_id).count()

def serialize_project(project: models.Project, db: Session) -> schemas.ProjectResponse:
    data = schemas.ProjectResponse.model_validate(project)
    data.task_count = project_task_count(db, project.id)
    return data

def filter_tasks_for_user(query, user: models.User):
    if user.role == auth.ROLE_DEVELOPER:
        return query.filter(
            (models.Task.assignee_id == user.id) | (models.Task.assignee_id.is_(None))
        )
    return query

def apply_task_status(task: models.Task, new_status: str):
    task.status = new_status
    if new_status == "Done":
        if not task.completed_at:
            task.completed_at = datetime.utcnow()
    else:
        task.completed_at = None

def serialize_user(user: models.User) -> schemas.UserResponse:
    data = schemas.UserResponse.model_validate(user)
    data.skill_list = schemas.parse_skills(user.skills)
    return data

def sprint_stats(db: Session, sprint_id: int):
    tasks = db.query(models.Task).filter(models.Task.sprint_id == sprint_id).all()
    return len(tasks), len([t for t in tasks if t.status == "Done"])

def serialize_sprint(sprint: models.Sprint, db: Session) -> schemas.SprintResponse:
    total, completed = sprint_stats(db, sprint.id)
    data = schemas.SprintResponse.model_validate(sprint)
    data.task_count = total
    data.completed_count = completed
    return data

DUE_NOTIFICATION_TYPES = ("overdue", "due_today", "due_soon")


def sync_due_notifications(db: Session, user: models.User):
    today_str = datetime.now().strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    if user.role == auth.ROLE_DEVELOPER:
        task_query = db.query(models.Task).filter(
            models.Task.assignee_id == user.id,
            models.Task.status != "Done",
            models.Task.due_date.isnot(None)
        )
    elif user.role in [auth.ROLE_ADMIN, auth.ROLE_MANAGER]:
        task_query = db.query(models.Task).filter(
            models.Task.status != "Done",
            models.Task.due_date.isnot(None)
        )
    else:
        return

    for task in task_query.all():
        recipient_id = task.assignee_id or user.id
        if user.role == auth.ROLE_DEVELOPER and recipient_id != user.id:
            continue

        if task.due_date < today_str:
            ntype, title = "overdue", f"Overdue: {task.title}"
        elif task.due_date == today_str:
            ntype, title = "due_today", f"Due today: {task.title}"
        elif task.due_date == tomorrow_str:
            ntype, title = "due_soon", f"Due tomorrow: {task.title}"
        else:
            continue

        message = f'Task "{task.title}" needs attention ({ntype.replace("_", " ")})'

        # One alert per user+task — do not recreate after mark-as-read
        existing = db.query(models.Notification).filter(
            models.Notification.user_id == recipient_id,
            models.Notification.task_id == task.id,
            models.Notification.type.in_(DUE_NOTIFICATION_TYPES),
        ).first()

        if existing:
            old_type = existing.type
            if existing.type != ntype or existing.title != title or existing.message != message:
                existing.type = ntype
                existing.title = title
                existing.message = message
                if ntype == "overdue" and old_type != "overdue":
                    existing.is_read = False
        else:
            db.add(models.Notification(
                user_id=recipient_id,
                task_id=task.id,
                type=ntype,
                title=title,
                message=message
            ))
    db.commit()

# --- Auth Routes ---
def set_session_cookie(request: Request, response: Response, user: models.User):
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=auth.create_access_token(user),
        httponly=True,
        max_age=int(auth.TOKEN_TTL.total_seconds()),
        samesite="lax",
        # Secure whenever the browser is on HTTPS (cloudflared forwards the
        # scheme); plain-HTTP local development still works.
        secure=request.url.scheme == "https",
        path="/",
    )


@app.post("/api/auth/login")
def login(request: Request, response: Response, form_data: schemas.LoginRequest, db: Session = Depends(get_db)):
    ip = auth.client_ip(request)
    retry_after = auth.login_retry_after(ip, form_data.username)
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {retry_after // 60 + 1} minute(s).",
            headers={"Retry-After": str(retry_after)},
        )

    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.password_hash):
        auth.record_login_failure(ip, form_data.username)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect username or password"
        )

    auth.clear_login_failures(ip, form_data.username)
    set_session_cookie(request, response, user)
    return {"status": "success", "username": user.username, "role": user.role, "full_name": user.full_name}

@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"status": "success", "message": "Logged out"}

@app.get("/api/auth/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    return serialize_user(current_user)

@app.post("/api/auth/reset-password")
def reset_password(
    data: schemas.PasswordReset,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not auth.verify_password(data.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect old password")

    current_user.password_hash = auth.hash_password(data.new_password)
    # Sign out every other session (e.g. a stolen cookie), keep this one.
    auth.revoke_sessions(current_user)
    db.commit()
    set_session_cookie(request, response, current_user)
    log_activity(db, current_user.id, "Password Reset", f"Changed password for {current_user.username}")
    return {"status": "success", "message": "Password changed successfully"}


# --- User Management Routes ---
def _admin_count(db: Session) -> int:
    return db.query(models.User).filter(models.User.role == auth.ROLE_ADMIN).count()

@app.get("/api/users", response_model=List[schemas.UserResponse])
def list_users(
    skill: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    users = db.query(models.User).all()
    result = []
    for user in users:
        data = serialize_user(user)
        if skill and skill.lower() not in [s.lower() for s in data.skill_list]:
            continue
        result.append(data)
    return result


@app.post("/api/users", response_model=schemas.UserResponse)
def create_user(
    user_in: schemas.UserCreate,
    db: Session = Depends(get_db),
    admin_user: models.User = Depends(auth.check_permission(["Admin"]))
):
    # Check if exists
    exists = db.query(models.User).filter(models.User.username == user_in.username).first()
    if exists:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    hashed_pw = auth.hash_password(user_in.password)
    user = models.User(
        username=user_in.username,
        password_hash=hashed_pw,
        role=user_in.role,
        full_name=user_in.full_name,
        skills=user_in.skills or ""
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    log_activity(db, admin_user.id, "Create User", f"Created user {user.username} with role {user.role}")
    return serialize_user(user)

@app.put("/api/users/{user_id}", response_model=schemas.UserResponse)
def update_user(
    user_id: int,
    user_in: schemas.UserUpdate,
    db: Session = Depends(get_db),
    admin_user: models.User = Depends(auth.check_permission(["Admin"]))
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if (user_in.role is not None and user.role == auth.ROLE_ADMIN
            and user_in.role != auth.ROLE_ADMIN and _admin_count(db) <= 1):
        raise HTTPException(status_code=400, detail="Cannot remove the last Admin. Promote another user first.")

    if user_in.full_name is not None:
        user.full_name = user_in.full_name
    if user_in.role is not None and user_in.role != user.role:
        user.role = user_in.role
        auth.revoke_sessions(user)
    if user_in.skills is not None:
        user.skills = user_in.skills
    if user_in.password:
        user.password_hash = auth.hash_password(user_in.password)
        auth.revoke_sessions(user)

    db.commit()
    db.refresh(user)
    
    log_activity(db, admin_user.id, "Update User", f"Updated user {user.username}")
    return serialize_user(user)

@app.delete("/api/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin_user: models.User = Depends(auth.check_permission(["Admin"]))
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    if user.role == auth.ROLE_ADMIN and _admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last Admin.")
        
    username = user.username
    db.delete(user)
    db.commit()
    
    log_activity(db, admin_user.id, "Delete User", f"Deleted user {username}")
    return {"status": "success", "message": f"User {username} deleted"}

# --- Task Routes ---
def check_refs(db: Session, *, assignee_id=None, project_id=None, sprint_id=None, label_ids=None, lead_id=None):
    """400 for ids that do not exist, instead of a foreign-key 500 or a silent drop."""
    for model, value, label in ((models.User, assignee_id, "Assignee"), (models.User, lead_id, "Project lead"),
                                (models.Project, project_id, "Project"), (models.Sprint, sprint_id, "Sprint")):
        if value is not None and db.query(model.id).filter(model.id == value).first() is None:
            raise HTTPException(status_code=400, detail=f"{label} {value} does not exist.")
    if label_ids:
        found = {row.id for row in db.query(models.Label.id).filter(models.Label.id.in_(label_ids))}
        missing = sorted(set(label_ids) - found)
        if missing:
            raise HTTPException(status_code=400, detail=f"Unknown label id(s): {missing}")

@app.get("/api/tasks", response_model=List[schemas.TaskResponse])
def get_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
    assignee_id: Optional[int] = None,
    project_id: Optional[int] = None,
    sprint_id: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    query = db.query(models.Task)
    query = filter_tasks_for_user(query, current_user)
    
    if status:
        query = query.filter(models.Task.status == status)
    if priority:
        query = query.filter(models.Task.priority == priority)
    if category:
        query = query.filter(models.Task.category == category)
    if project_id is not None:
        query = query.filter(models.Task.project_id == project_id)
    if sprint_id is not None:
        query = query.filter(models.Task.sprint_id == sprint_id)
    if assignee_id is not None:
        if current_user.role == auth.ROLE_DEVELOPER and assignee_id != current_user.id:
            raise HTTPException(status_code=403, detail="Developers can only view their own or unassigned tasks.")
        query = query.filter(models.Task.assignee_id == assignee_id)
    if search:
        query = query.filter(
            models.Task.title.like(f"%{search}%") | models.Task.description.like(f"%{search}%")
        )
        
    return query.order_by(models.Task.created_at.desc()).all()

@app.get("/api/tasks/{task_id}", response_model=schemas.TaskDetailResponse)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not auth.developer_can_access_task(current_user, task):
        raise HTTPException(status_code=403, detail="Access denied to this task.")
    return task

@app.post("/api/tasks", response_model=schemas.TaskResponse)
def create_task(
    task_in: schemas.TaskCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    if (current_user.role == auth.ROLE_DEVELOPER and task_in.assignee_id is not None
            and task_in.assignee_id != current_user.id):
        raise HTTPException(status_code=403, detail="Developers can only assign tasks to themselves.")
    check_refs(db, assignee_id=task_in.assignee_id, project_id=task_in.project_id,
               sprint_id=task_in.sprint_id, label_ids=task_in.label_ids)

    task = models.Task(
        title=task_in.title,
        description=task_in.description,
        priority=task_in.priority,
        status=task_in.status,
        category=task_in.category,
        due_date=task_in.due_date,
        estimated_hours=task_in.estimated_hours,
        actual_hours=task_in.actual_hours,
        assignee_id=task_in.assignee_id,
        project_id=task_in.project_id,
        sprint_id=task_in.sprint_id
    )
    if task_in.status == "Done":
        task.completed_at = datetime.utcnow()
    
    # Manage labels
    if task_in.label_ids:
        labels = db.query(models.Label).filter(models.Label.id.in_(task_in.label_ids)).all()
        task.labels = labels
        
    db.add(task)
    db.commit()
    db.refresh(task)
    
    log_activity(db, current_user.id, "Create Task", f"Created task: {task.title}", task.id)
    return task

@app.put("/api/tasks/{task_id}", response_model=schemas.TaskResponse)
def update_task(
    task_id: int,
    task_in: schemas.TaskUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not auth.developer_can_access_task(current_user, task):
        raise HTTPException(status_code=403, detail="You can only modify tasks assigned to you or unassigned tasks.")
    if current_user.role == auth.ROLE_DEVELOPER and task_in.assignee_id is not None and task_in.assignee_id != current_user.id:
        raise HTTPException(status_code=403, detail="Developers cannot reassign tasks to other team members.")
    check_refs(db, assignee_id=task_in.assignee_id, project_id=task_in.project_id,
               sprint_id=task_in.sprint_id, label_ids=task_in.label_ids)

    fields_to_update = ["title", "description", "priority", "status", "category", "due_date", "estimated_hours", "actual_hours", "assignee_id", "project_id", "sprint_id"]
    for field in fields_to_update:
        val = getattr(task_in, field)
        if val is not None:
            if field == "status":
                if task.status != val:
                    log_activity(db, current_user.id, "Status Change", f"Moved task to '{val}'", task.id)
                apply_task_status(task, val)
            else:
                setattr(task, field, val)
            
    if task_in.label_ids is not None:
        labels = db.query(models.Label).filter(models.Label.id.in_(task_in.label_ids)).all()
        task.labels = labels
        
    db.commit()
    db.refresh(task)
    
    log_activity(db, current_user.id, "Update Task", f"Updated task details", task.id)
    return task

@app.patch("/api/tasks/{task_id}/status", response_model=schemas.TaskResponse)
def update_task_status(
    task_id: int,
    status_update: schemas.TaskStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not auth.developer_can_access_task(current_user, task):
        raise HTTPException(status_code=403, detail="You can only modify tasks assigned to you or unassigned tasks.")
        
    new_status = status_update.status
    old_status = task.status
    if new_status == old_status:
        return task

    apply_task_status(task, new_status)
    db.commit()
    db.refresh(task)
    
    log_activity(db, current_user.id, "Move Task", f"Moved from '{old_status}' to '{new_status}'", task.id)
    return task

@app.delete("/api/tasks/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_BULK))
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    title = task.title
    db.delete(task)
    db.commit()
    
    log_activity(db, current_user.id, "Delete Task", f"Deleted task: {title}")
    return {"status": "success", "message": f"Task '{title}' deleted"}

@app.post("/api/tasks/bulk")
def bulk_task_action(
    bulk_in: schemas.BulkTaskAction,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_BULK))
):
    if not bulk_in.task_ids:
        raise HTTPException(status_code=400, detail="No tasks selected")

    if bulk_in.action == "status" and not bulk_in.status:
        raise HTTPException(status_code=400, detail="status is required for a status change")
    if bulk_in.action == "priority" and not bulk_in.priority:
        raise HTTPException(status_code=400, detail="priority is required for a priority change")
    check_refs(db, assignee_id=bulk_in.assignee_id if bulk_in.action == "assign" else None,
               project_id=bulk_in.project_id if bulk_in.action == "project" else None,
               sprint_id=bulk_in.sprint_id if bulk_in.action == "sprint" else None)

    task_ids = set(bulk_in.task_ids)
    tasks = db.query(models.Task).filter(models.Task.id.in_(task_ids)).all()
    if len(tasks) != len(task_ids):
        raise HTTPException(status_code=404, detail="One or more tasks not found")

    updated = 0
    action = bulk_in.action

    if action == "delete":
        for task in tasks:
            db.delete(task)
            updated += 1
        db.commit()
        log_activity(db, current_user.id, "Bulk Delete", f"Deleted {updated} tasks")
        return {"status": "success", "updated": updated}

    for task in tasks:
        if action == "assign":
            task.assignee_id = bulk_in.assignee_id
            updated += 1
        elif action == "status" and bulk_in.status:
            apply_task_status(task, bulk_in.status)
            updated += 1
        elif action == "priority" and bulk_in.priority:
            task.priority = bulk_in.priority
            updated += 1
        elif action == "project":
            task.project_id = bulk_in.project_id
            updated += 1
        elif action == "sprint":
            task.sprint_id = bulk_in.sprint_id
            updated += 1
        else:
            raise HTTPException(status_code=400, detail=f"Invalid bulk action: {action}")

    db.commit()
    log_activity(db, current_user.id, "Bulk Update", f"Bulk {action} on {updated} tasks")
    return {"status": "success", "updated": updated}

# --- Project Routes ---
@app.get("/api/projects", response_model=List[schemas.ProjectResponse])
def list_projects(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    query = db.query(models.Project)
    if status:
        query = query.filter(models.Project.status == status)
    projects = query.order_by(models.Project.name).all()
    return [serialize_project(p, db) for p in projects]

@app.get("/api/projects/{project_id}", response_model=schemas.ProjectResponse)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return serialize_project(project, db)

@app.post("/api/projects", response_model=schemas.ProjectResponse)
def create_project(
    project_in: schemas.ProjectCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_PROJECT_MANAGE))
):
    check_refs(db, lead_id=project_in.lead_id)
    project = models.Project(
        name=project_in.name,
        description=project_in.description,
        status=project_in.status,
        lead_id=project_in.lead_id
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    log_activity(db, current_user.id, "Create Project", f"Created project: {project.name}")
    return serialize_project(project, db)

@app.put("/api/projects/{project_id}", response_model=schemas.ProjectResponse)
def update_project(
    project_id: int,
    project_in: schemas.ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_PROJECT_MANAGE))
):
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    check_refs(db, lead_id=project_in.lead_id)

    for field in ["name", "description", "status", "lead_id"]:
        val = getattr(project_in, field)
        if val is not None:
            setattr(project, field, val)

    db.commit()
    db.refresh(project)
    log_activity(db, current_user.id, "Update Project", f"Updated project: {project.name}")
    return serialize_project(project, db)

@app.delete("/api/projects/{project_id}")
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission([auth.ROLE_ADMIN]))
):
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    name = project.name
    db.query(models.Task).filter(models.Task.project_id == project_id).update({"project_id": None})
    db.delete(project)
    db.commit()
    log_activity(db, current_user.id, "Delete Project", f"Deleted project: {name}")
    return {"status": "success", "message": f"Project '{name}' deleted"}

# --- Subtask Routes ---
@app.post("/api/tasks/{task_id}/subtasks", response_model=schemas.SubtaskResponse)
def create_subtask(
    task_id: int,
    subtask_in: schemas.SubtaskCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not auth.developer_can_access_task(current_user, task):
        raise HTTPException(status_code=403, detail="You do not have access to this task.")

    max_order = db.query(models.Subtask).filter(models.Subtask.task_id == task_id).count()
    subtask = models.Subtask(
        task_id=task_id,
        title=subtask_in.title.strip(),
        sort_order=max_order
    )
    db.add(subtask)
    db.commit()
    db.refresh(subtask)
    log_activity(db, current_user.id, "Add Subtask", f"Added subtask to '{task.title}'", task.id)
    return subtask

@app.put("/api/subtasks/{subtask_id}", response_model=schemas.SubtaskResponse)
def update_subtask(
    subtask_id: int,
    subtask_in: schemas.SubtaskUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    subtask = db.query(models.Subtask).filter(models.Subtask.id == subtask_id).first()
    if not subtask:
        raise HTTPException(status_code=404, detail="Subtask not found")

    task = db.query(models.Task).filter(models.Task.id == subtask.task_id).first()
    if task and not auth.developer_can_access_task(current_user, task):
        raise HTTPException(status_code=403, detail="You do not have access to this task.")

    if subtask_in.title is not None:
        subtask.title = subtask_in.title.strip()
    if subtask_in.is_completed is not None:
        subtask.is_completed = subtask_in.is_completed
    if subtask_in.sort_order is not None:
        subtask.sort_order = subtask_in.sort_order

    db.commit()
    db.refresh(subtask)
    return subtask

@app.delete("/api/subtasks/{subtask_id}")
def delete_subtask(
    subtask_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    subtask = db.query(models.Subtask).filter(models.Subtask.id == subtask_id).first()
    if not subtask:
        raise HTTPException(status_code=404, detail="Subtask not found")

    task = db.query(models.Task).filter(models.Task.id == subtask.task_id).first()
    if task and not auth.developer_can_access_task(current_user, task):
        raise HTTPException(status_code=403, detail="You do not have access to this task.")

    task_id = subtask.task_id
    db.delete(subtask)
    db.commit()
    log_activity(db, current_user.id, "Delete Subtask", "Removed subtask", task_id)
    return {"status": "success", "message": "Subtask deleted"}

# --- Sprint Routes ---
@app.get("/api/sprints", response_model=List[schemas.SprintResponse])
def list_sprints(
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    query = db.query(models.Sprint)
    if project_id is not None:
        query = query.filter(models.Sprint.project_id == project_id)
    if status:
        query = query.filter(models.Sprint.status == status)
    sprints = query.order_by(models.Sprint.start_date.desc()).all()
    return [serialize_sprint(s, db) for s in sprints]

@app.get("/api/sprints/{sprint_id}", response_model=schemas.SprintResponse)
def get_sprint(
    sprint_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    return serialize_sprint(sprint, db)

@app.post("/api/sprints", response_model=schemas.SprintResponse)
def create_sprint(
    sprint_in: schemas.SprintCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_PROJECT_MANAGE))
):
    check_refs(db, project_id=sprint_in.project_id)
    sprint = models.Sprint(
        name=sprint_in.name,
        project_id=sprint_in.project_id,
        start_date=sprint_in.start_date,
        end_date=sprint_in.end_date,
        goal=sprint_in.goal,
        status=sprint_in.status
    )
    db.add(sprint)
    db.commit()
    db.refresh(sprint)
    log_activity(db, current_user.id, "Create Sprint", f"Created sprint: {sprint.name}")
    return serialize_sprint(sprint, db)

@app.put("/api/sprints/{sprint_id}", response_model=schemas.SprintResponse)
def update_sprint(
    sprint_id: int,
    sprint_in: schemas.SprintUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_PROJECT_MANAGE))
):
    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    check_refs(db, project_id=sprint_in.project_id)
    start = sprint_in.start_date or sprint.start_date
    end = sprint_in.end_date or sprint.end_date
    if end < start:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")

    for field in ["name", "project_id", "start_date", "end_date", "goal", "status"]:
        val = getattr(sprint_in, field)
        if val is not None:
            setattr(sprint, field, val)

    db.commit()
    db.refresh(sprint)
    log_activity(db, current_user.id, "Update Sprint", f"Updated sprint: {sprint.name}")
    return serialize_sprint(sprint, db)

@app.delete("/api/sprints/{sprint_id}")
def delete_sprint(
    sprint_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission([auth.ROLE_ADMIN]))
):
    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")

    name = sprint.name
    db.query(models.Task).filter(models.Task.sprint_id == sprint_id).update({"sprint_id": None})
    db.delete(sprint)
    db.commit()
    log_activity(db, current_user.id, "Delete Sprint", f"Deleted sprint: {name}")
    return {"status": "success", "message": f"Sprint '{name}' deleted"}

@app.get("/api/sprints/{sprint_id}/burndown")
def get_sprint_burndown(
    sprint_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")

    tasks = db.query(models.Task).filter(models.Task.sprint_id == sprint_id).all()
    total_scope = len(tasks)
    completed = len([t for t in tasks if t.status == "Done"])

    from datetime import date as date_cls
    start = date_cls.fromisoformat(sprint.start_date)
    end = date_cls.fromisoformat(sprint.end_date)
    today = date_cls.today()
    last_day = min(end, today)

    dates = []
    actual_remaining = []
    ideal_remaining = []
    day_span = max((end - start).days, 1)

    current = start
    day_index = 0
    while current <= last_day:
        dates.append(current.isoformat())
        remaining = 0
        for task in tasks:
            if task.status != "Done":
                remaining += 1
            elif task.completed_at and task.completed_at.date() > current:
                remaining += 1
        actual_remaining.append(remaining)
        ideal_remaining.append(round(total_scope * (1 - day_index / day_span), 1))
        day_index += 1
        current += timedelta(days=1)

    sprint_days = max((end - start).days + 1, 1)
    velocity = round(completed / sprint_days * 7, 1)

    return {
        "sprint_id": sprint.id,
        "sprint_name": sprint.name,
        "total_scope": total_scope,
        "completed": completed,
        "velocity_per_week": velocity,
        "dates": dates,
        "actual_remaining": actual_remaining,
        "ideal_remaining": ideal_remaining
    }

# --- Notification Routes ---
@app.get("/api/notifications", response_model=List[schemas.NotificationResponse])
def list_notifications(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    sync_due_notifications(db, current_user)
    return db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id
    ).order_by(models.Notification.created_at.desc()).limit(50).all()

@app.get("/api/notifications/unread-count")
def unread_notification_count(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    count = db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id,
        models.Notification.is_read == False
    ).count()
    return {"count": count}

@app.patch("/api/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    note = db.query(models.Notification).filter(
        models.Notification.id == notification_id,
        models.Notification.user_id == current_user.id
    ).first()
    if not note:
        raise HTTPException(status_code=404, detail="Notification not found")
    note.is_read = True
    db.commit()
    return {"status": "success"}

@app.post("/api/notifications/mark-all-read")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    db.query(models.Notification).filter(
        models.Notification.user_id == current_user.id,
        models.Notification.is_read == False
    ).update({"is_read": True})
    db.commit()
    return {"status": "success"}

# --- Comments Routes ---
@app.post("/api/tasks/{task_id}/comments", response_model=schemas.CommentResponse)
def add_comment(
    task_id: int,
    comment_in: schemas.CommentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not auth.developer_can_access_task(current_user, task):
        raise HTTPException(status_code=403, detail="You do not have access to this task.")
        
    comment = models.Comment(
        task_id=task_id,
        user_id=current_user.id,
        content=comment_in.content
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    
    log_activity(db, current_user.id, "Comment Task", f"Added comment", task.id)
    return comment

@app.delete("/api/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    comment = db.query(models.Comment).filter(models.Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
        
    if current_user.role not in [auth.ROLE_ADMIN, auth.ROLE_MANAGER]:
        if comment.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to delete this comment")
        task = db.query(models.Task).filter(models.Task.id == comment.task_id).first()
        if task and not auth.developer_can_access_task(current_user, task):
            raise HTTPException(status_code=403, detail="Not authorized to access this task's comment")
        
    task_id = comment.task_id
    db.delete(comment)
    db.commit()
    
    log_activity(db, current_user.id, "Delete Comment", "Deleted comment", task_id)
    return {"status": "success", "message": "Comment deleted"}

# --- Label Routes ---
@app.get("/api/labels", response_model=List[schemas.LabelResponse])
def get_labels(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    return db.query(models.Label).all()

@app.post("/api/labels", response_model=schemas.LabelResponse)
def create_label(
    label_in: schemas.LabelCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    # Check if exists
    exists = db.query(models.Label).filter(models.Label.name == label_in.name).first()
    if exists:
        raise HTTPException(status_code=400, detail="Label name already exists")
        
    label = models.Label(name=label_in.name, color=label_in.color)
    db.add(label)
    db.commit()
    db.refresh(label)
    
    log_activity(db, current_user.id, "Create Label", f"Created label: {label.name}")
    return label

@app.delete("/api/labels/{label_id}")
def delete_label(
    label_id: int,
    db: Session = Depends(get_db),
    admin_user: models.User = Depends(auth.check_permission([auth.ROLE_ADMIN, auth.ROLE_MANAGER]))
):
    label = db.query(models.Label).filter(models.Label.id == label_id).first()
    if not label:
        raise HTTPException(status_code=404, detail="Label not found")
        
    name = label.name
    db.delete(label)
    db.commit()
    
    log_activity(db, admin_user.id, "Delete Label", f"Deleted label: {name}")
    return {"status": "success", "message": f"Label '{name}' deleted"}

# --- Attachments Routes ---
MAX_UPLOAD_BYTES = config.get_int("MAX_UPLOAD_MB", 25) * 1024 * 1024
UPLOAD_CHUNK = 1024 * 1024


def _safe_display_name(name: Optional[str]) -> str:
    # Keep the user's filename for display/download only; it never touches the
    # filesystem path, so "../" or "/" in it cannot escape uploads/.
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base)
    return base[:200] or "file"


# Plain def (not async): the work is blocking file and DB I/O, which would
# otherwise stall the event loop for every other request.
@app.post("/api/tasks/{task_id}/attachments", response_model=schemas.AttachmentResponse)
def upload_attachment(
    task_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not auth.developer_can_access_task(current_user, task):
        raise HTTPException(status_code=403, detail="You do not have access to this task.")

    display_name = _safe_display_name(file.filename)
    ext = os.path.splitext(display_name)[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", ext):
        ext = ""
    # Random stored name: no collisions between same-named uploads, and nothing
    # user-controlled in the path.
    file_path = os.path.join("uploads", f"{uuid.uuid4().hex}{ext}")

    size = 0
    try:
        with open(file_path, "wb") as buffer:
            while chunk := file.file.read(UPLOAD_CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413,
                                        detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
                buffer.write(chunk)
    except BaseException:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise

    attachment = models.Attachment(
        task_id=task_id,
        user_id=current_user.id,
        filename=display_name,
        file_path=file_path,
        file_size=size
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)

    log_activity(db, current_user.id, "Upload File", f"Uploaded file: {display_name}", task.id)
    return attachment

@app.get("/api/attachments/{attachment_id}")
def download_attachment(
    attachment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    attachment = db.query(models.Attachment).filter(models.Attachment.id == attachment_id).first()
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if current_user.role == auth.ROLE_DEVELOPER:
        task = db.query(models.Task).filter(models.Task.id == attachment.task_id).first()
        if task and not auth.developer_can_access_task(current_user, task):
            raise HTTPException(status_code=403, detail="Access denied to this file.")

    # Only ever serve files that really live in uploads/.
    uploads_dir = os.path.realpath("uploads")
    real_path = os.path.realpath(attachment.file_path)
    if not real_path.startswith(uploads_dir + os.sep) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="File not found on server")

    # Always a download (Content-Disposition: attachment, octet-stream, nosniff),
    # so an uploaded .html/.svg can never render as a page on this origin.
    return FileResponse(
        path=real_path,
        filename=attachment.filename,
        media_type="application/octet-stream",
        content_disposition_type="attachment",
    )

@app.delete("/api/attachments/{attachment_id}")
def delete_attachment(
    attachment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    attachment = db.query(models.Attachment).filter(models.Attachment.id == attachment_id).first()
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
        
    if current_user.role not in [auth.ROLE_ADMIN, auth.ROLE_MANAGER]:
        if attachment.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to delete this file")
        task = db.query(models.Task).filter(models.Task.id == attachment.task_id).first()
        if task and not auth.developer_can_access_task(current_user, task):
            raise HTTPException(status_code=403, detail="Access denied to this task.")
        
    # Delete physical file
    if os.path.exists(attachment.file_path):
        try:
            os.remove(attachment.file_path)
        except Exception as e:
            print(f"Error removing file: {e}")
            
    task_id = attachment.task_id
    filename = attachment.filename
    db.delete(attachment)
    db.commit()
    
    log_activity(db, current_user.id, "Delete File", f"Deleted file: {filename}", task_id)
    return {"status": "success", "message": "Attachment deleted"}

# --- Analytics Routes ---
@app.get("/api/analytics")
def get_analytics(
    project_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    query = db.query(models.Task)
    query = filter_tasks_for_user(query, current_user)
    if project_id is not None:
        query = query.filter(models.Task.project_id == project_id)
    tasks = query.all()
    users = db.query(models.User).all()
    
    total = len(tasks)
    completed = len([t for t in tasks if t.status == "Done"])
    pending = total - completed
    
    # Calculate velocity or completion rate
    progress_percent = (completed / total * 100) if total > 0 else 0
    
    # Count by status
    status_counts = {
        "Backlog": 0, "Todo": 0, "In Progress": 0,
        "Review": 0, "Testing": 0, "Done": 0
    }
    for t in tasks:
        if t.status in status_counts:
            status_counts[t.status] += 1
            
    # Count by category
    categories = ["Backend", "Frontend", "AI", "Database", "DevOps", "Documentation", "Testing", "Other"]
    category_counts = {cat: 0 for cat in categories}
    for t in tasks:
        if t.category in category_counts:
            category_counts[t.category] += 1
            
    # Workload by User (assignee name: active tasks count)
    workload = {u.full_name or u.username: 0 for u in users}
    workload["Unassigned"] = 0
    for t in tasks:
        if t.status != "Done":
            if t.assignee:
                name = t.assignee.full_name or t.assignee.username
                workload[name] = workload.get(name, 0) + 1
            else:
                workload["Unassigned"] += 1
                
    # Hours summary
    est_total = sum(t.estimated_hours for t in tasks)
    act_total = sum(t.actual_hours for t in tasks)
    
    from datetime import date as date_cls

    # Overdue tasks
    overdue_count = 0
    today_str = datetime.now().strftime("%Y-%m-%d")
    for t in tasks:
        if t.status != "Done" and t.due_date and t.due_date < today_str:
            overdue_count += 1

    # Completed this week (Mon-Sun)
    week_start = datetime.now() - timedelta(days=datetime.now().weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    completed_this_week = 0
    for t in tasks:
        if t.status == "Done" and t.completed_at and t.completed_at >= week_start:
            completed_this_week += 1

    # Per-developer completion rate
    dev_stats = {}
    for u in users:
        if u.role not in [auth.ROLE_DEVELOPER, auth.ROLE_MANAGER]:
            continue
        name = u.full_name or u.username
        assigned = [t for t in tasks if t.assignee_id == u.id]
        done = [t for t in assigned if t.status == "Done"]
        dev_stats[name] = {
            "assigned": len(assigned),
            "completed": len(done),
            "rate": round(len(done) / len(assigned) * 100, 1) if assigned else 0
        }

    # Active sprint velocity snapshot
    active_sprint = db.query(models.Sprint).filter(models.Sprint.status == "Active").order_by(models.Sprint.start_date.desc()).first()
    sprint_velocity = None
    if active_sprint:
        sprint_tasks = db.query(models.Task).filter(models.Task.sprint_id == active_sprint.id).all()
        sprint_done = len([t for t in sprint_tasks if t.status == "Done"])
        sprint_days = max((date_cls.fromisoformat(active_sprint.end_date) - date_cls.fromisoformat(active_sprint.start_date)).days + 1, 1)
        sprint_velocity = {
            "sprint_id": active_sprint.id,
            "sprint_name": active_sprint.name,
            "completed": sprint_done,
            "total": len(sprint_tasks),
            "velocity_per_week": round(sprint_done / sprint_days * 7, 1)
        }
            
    # Recent logs
    if current_user.role == auth.ROLE_DEVELOPER:
        query = db.query(models.ActivityLog).join(models.Task, isouter=True)
        query = query.filter((models.ActivityLog.task_id.is_(None)) | (models.Task.assignee_id == current_user.id) | (models.Task.assignee_id.is_(None)))
        logs = query.order_by(models.ActivityLog.timestamp.desc()).limit(15).all()
    else:
        logs = db.query(models.ActivityLog).order_by(models.ActivityLog.timestamp.desc()).limit(15).all()
    recent_logs = []
    for l in logs:
        # Load task title if available
        task_title = None
        if l.task_id:
            t = db.query(models.Task).filter(models.Task.id == l.task_id).first()
            if t:
                task_title = t.title
                
        recent_logs.append({
            "id": l.id,
            "username": l.user.username,
            "full_name": l.user.full_name,
            "action": l.action,
            "details": l.details,
            "task_id": l.task_id,
            "task_title": task_title,
            "timestamp": l.timestamp.isoformat()
        })
        
    return {
        "summary": {
            "total": total,
            "completed": completed,
            "pending": pending,
            "overdue": overdue_count,
            "completed_this_week": completed_this_week,
            "progress_percent": round(progress_percent, 1),
            "estimated_hours": est_total,
            "actual_hours": act_total
        },
        "status_distribution": status_counts,
        "category_distribution": category_counts,
        "workload_distribution": workload,
        "developer_completion": dev_stats,
        "sprint_velocity": sprint_velocity,
        "recent_logs": recent_logs
    }

# --- Backup Management Routes ---
BACKUP_NAME_RE = re.compile(r"^database_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.(sql|db)$")


def _backup_path(filename: str) -> str:
    # Backup names are generated by create_backup; anything else is refused,
    # so a crafted name cannot reach files outside backups/.
    if not BACKUP_NAME_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    return os.path.join("backups", filename)


@app.get("/api/backups")
def list_backups(
    admin_user: models.User = Depends(auth.check_permission(["Admin"]))
):
    backup_files = []
    if os.path.exists("backups"):
        for f in os.listdir("backups"):
            if f.endswith(".db") or f.endswith(".sql"):
                path = os.path.join("backups", f)
                size = os.path.getsize(path)
                created = os.path.getctime(path)
                backup_files.append({
                    "filename": f,
                    "size_bytes": size,
                    "created_at": datetime.fromtimestamp(created).isoformat()
                })
    # Sort by created date descending
    backup_files.sort(key=lambda x: x["created_at"], reverse=True)
    return backup_files

@app.post("/api/backups")
def create_backup(
    db: Session = Depends(get_db),
    admin_user: models.User = Depends(auth.check_permission(["Admin"]))
):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"database_{timestamp}.sql"
    backup_path = os.path.join("backups", backup_filename)
    
    import subprocess
    from sqlalchemy.engine import make_url
    db_url = make_url(DATABASE_URL)
    container = os.environ.get("BACKUP_PG_CONTAINER", "postgres18")
    # Credentials come from DATABASE_URL via the environment, never argv or the
    # source. pg_dump runs inside the container, so connect to it locally.
    cmd = ["docker", "exec", "-i", "-e", "PGPASSWORD", container,
           "pg_dump", "-U", db_url.username or "postgres", db_url.database or ""]
    env = {**os.environ, "PGPASSWORD": db_url.password or ""}
    try:
        with open(backup_path, "w", encoding="utf-8") as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=env, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", "replace").strip()[:300] or
                               f"pg_dump exited with {result.returncode}")
        if os.path.getsize(backup_path) == 0:
            raise RuntimeError("pg_dump produced an empty file")
    except Exception as e:
        # Never leave a partial/empty dump that would be listed as a backup.
        if os.path.exists(backup_path):
            os.remove(backup_path)
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")

    log_activity(db, admin_user.id, "Database Backup", f"Created database backup: {backup_filename}")
    return {"status": "success", "filename": backup_filename}

@app.get("/api/backups/{filename}")
def download_backup(
    filename: str,
    admin_user: models.User = Depends(auth.check_permission(["Admin"]))
):
    path = _backup_path(filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Backup file not found")
    return FileResponse(
        path=path,
        filename=filename,
        media_type="application/octet-stream"
    )

@app.delete("/api/backups/{filename}")
def delete_backup(
    filename: str,
    db: Session = Depends(get_db),
    admin_user: models.User = Depends(auth.check_permission(["Admin"]))
):
    path = _backup_path(filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Backup file not found")

    try:
        os.remove(path)
        log_activity(db, admin_user.id, "Delete Backup", f"Deleted backup file: {filename}")
        return {"status": "success", "message": f"Backup {filename} deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete backup: {str(e)}")

# --- Biometric machine time test ---------------------------------------------
# Sets a machine's clock to a target time, waits, then puts it back. A machine
# left on the wrong clock timestamps every punch wrongly, so once the clock may
# have been touched, every exit path ends in a restore attempt:
#   - restore targets the *correct* time, never "original + elapsed" worked out
#     in advance. For the RS9N the clock service stamps the time when the device
#     collects the command, so queueing/polling delay cannot make it stale (it
#     used to: requests have sat 40s-2min before being queued, leaving the
#     device ~2 minutes behind after every test);
#   - a set that fails or is not confirmed is still restored. An unconfirmed
#     RS9N command stays queued and would otherwise land later; the restore is
#     queued behind it, and the device runs its queue in order;
#   - each restore is verified against the device clock and retried;
#   - a marker file records an unfinished test, so a crash or restart mid-test
#     is restored on the next startup, and no new test starts until it is.

TIME_TEST_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".time_test_state.json")
TIME_TEST_MIN_WAIT_SEC = 5
TIME_TEST_MAX_WAIT_SEC = 600
TIME_TEST_RESTORE_ATTEMPTS = 3
TIME_TEST_RESTORE_BACKOFF_SEC = (5, 15)
TIME_TEST_TOLERANCE_SEC = 5      # a restored clock must be within this of the real time
TIME_TEST_JOB_TTL_SEC = 3600     # finished jobs' logs are kept this long
RS9N_OFFLINE_AFTER_SEC = 120     # matches the clock service's own offline threshold
# How long the clock service waits for the device to confirm. Kept well under
# Cloudflare's 100s proxy timeout, since the service also waits ~15s for a fresh
# handshake before replying.
RS9N_SET_WAIT_SEC = 45
# The HTTP timeout must also cover requests sitting in the service's queue.
RS9N_SET_HTTP_TIMEOUT = 240

TIME_TEST_JOBS = {}
_time_test_lock = threading.Lock()          # one test or restore at a time
_time_test_active = {"task_id": None}


def _new_time_test_job(machine: str, kind: str) -> str:
    now = time.time()
    expired = [tid for tid, job in list(TIME_TEST_JOBS.items())
               if job["done"] and now - (job["finished"] or now) > TIME_TEST_JOB_TTL_SEC]
    for tid in expired:
        TIME_TEST_JOBS.pop(tid, None)
    task_id = str(uuid.uuid4())
    TIME_TEST_JOBS[task_id] = {"machine": machine, "kind": kind, "logs": [],
                               "done": False, "outcome": None, "finished": None}
    _time_test_active["task_id"] = task_id
    return task_id


def _finish_time_test_job(task_id: str, outcome: str):
    job = TIME_TEST_JOBS.get(task_id)
    if job is not None:
        job["outcome"] = outcome
        job["done"] = True
        job["finished"] = time.time()
    _time_test_active["task_id"] = None
    _time_test_lock.release()


def _time_test_logger(task_id: str):
    def add_log(step: str, msg: str, status: str = "info"):
        # flush: under systemd stdout is a pipe, and buffered lines otherwise
        # reach the journal minutes late with misleading timestamps.
        print(f"[Time Test {task_id[:8]}] {step}: {msg}", flush=True)
        job = TIME_TEST_JOBS.get(task_id)
        if job is not None:
            job["logs"].append({
                "step": step,
                "message": msg,
                "status": status,
                "time": datetime.now().strftime("%H:%M:%S"),
            })
    return add_log


def _save_time_test_state(state: dict):
    tmp = TIME_TEST_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, TIME_TEST_STATE_FILE)


def _load_time_test_state() -> Optional[dict]:
    try:
        with open(TIME_TEST_STATE_FILE, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {"machine": None}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # Unreadable marker: a test was still unfinished, but on which machine
        # is unknown. Restoring both is harmless.
        return {"machine": None}


def _clear_time_test_state():
    try:
        os.remove(TIME_TEST_STATE_FILE)
    except FileNotFoundError:
        pass


def _parse_service_time(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# RS9N: reached through the attendance API (see rs9n_call).

def _rs9n_read_clock(attempts: int = 3) -> dict:
    last_error = None
    for attempt in range(attempts):
        try:
            return rs9n_call("GET", "/api/clock", timeout=30).get("data") or {}
        except RuntimeError as e:
            last_error = e
            if attempt < attempts - 1:
                time.sleep(3)
    raise last_error


def _rs9n_preflight(add_log) -> bool:
    add_log("connect", "Contacting attendance API...", "pending")
    try:
        clock = _rs9n_read_clock()
    except RuntimeError as e:
        add_log("error", f"{e} Nothing was changed on the machine.", "error")
        return False

    device_id = clock.get("device_id", "?")
    device_time = _parse_service_time(clock.get("device_time"))
    last_contact = _parse_service_time(clock.get("server_time_at_last_contact"))
    service_now = _parse_service_time(clock.get("server_time_now"))
    if device_time is None or last_contact is None or service_now is None:
        add_log("error", f"RS9N {device_id} has not reported a usable clock yet. "
                         "Nothing was changed on the machine.", "error")
        return False

    silent_for = (service_now - last_contact).total_seconds()
    if silent_for > RS9N_OFFLINE_AFTER_SEC:
        add_log("error", f"RS9N {device_id} last contacted the clock service "
                         f"{int(silent_for)}s ago and looks offline, so it could not "
                         "collect the change. Nothing was changed on the machine.", "error")
        return False

    add_log("connect", f"Connected to RS9N {device_id}. Device time: {device_time}", "success")
    drift = clock.get("drift_seconds")
    if drift is not None and abs(drift) > TIME_TEST_TOLERANCE_SEC:
        add_log("connect", f"Note: the device clock is already {clock.get('drift_human') or drift}; "
                           "the restore will put it on the correct time.", "info")
    return True


def _rs9n_set(add_log, target_dt: datetime) -> bool:
    add_log("set", f"Setting device time to: {target_dt} "
                   "(queued; applied when the device next polls)", "pending")
    try:
        res = rs9n_call("POST", "/api/set-time", {
            "time": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "wait_seconds": RS9N_SET_WAIT_SEC,
        }, timeout=RS9N_SET_HTTP_TIMEOUT)
    except RuntimeError as e:
        add_log("error", f"Setting the time failed: {e} It may still have been "
                         "queued, so restoring to be safe.", "error")
        return False

    if not res.get("confirmed"):
        add_log("error", f"The RS9N did not confirm within {RS9N_SET_WAIT_SEC}s. The change "
                         "stays queued and would apply later, so restoring now.", "error")
        return False
    if res.get("return_code") not in (None, "OK"):
        add_log("error", f"The RS9N answered '{res.get('return_code')}' to the time change. "
                         "Restoring to be safe.", "error")
        return False
    add_log("set", "Time set successfully on the machine.", "success")
    return True


def _rs9n_restore(add_log) -> bool:
    for attempt in range(1, TIME_TEST_RESTORE_ATTEMPTS + 1):
        add_log("restore", f"Restoring device time to the correct time "
                           f"(attempt {attempt}/{TIME_TEST_RESTORE_ATTEMPTS})...", "pending")
        try:
            # No "time": the service stamps its own clock when the device collects
            # the command, so the value cannot go stale while it is queued.
            res = rs9n_call("POST", "/api/set-time", {"wait_seconds": RS9N_SET_WAIT_SEC},
                            timeout=RS9N_SET_HTTP_TIMEOUT)
            if not res.get("confirmed"):
                add_log("restore", f"Not confirmed yet: {res.get('message', 'no reply from device')}",
                        "warning")
            else:
                after = res.get("clock_after") or {}
                drift = after.get("drift_seconds")
                if drift is None:
                    # Confirmed, but the device has not polled again; read it once more.
                    time.sleep(10)
                    drift = _rs9n_read_clock().get("drift_seconds")
                if drift is not None and abs(drift) <= TIME_TEST_TOLERANCE_SEC:
                    add_log("restore", f"Machine time restored successfully ({drift:+.0f}s from "
                                       "correct time). Test complete!", "success")
                    return True
                add_log("restore", f"Device confirmed, but its clock is still off "
                                   f"({drift if drift is not None else 'unknown'}s).", "warning")
        except RuntimeError as e:
            add_log("restore", f"Restore attempt failed: {e}", "warning")
        if attempt < TIME_TEST_RESTORE_ATTEMPTS:
            time.sleep(TIME_TEST_RESTORE_BACKOFF_SEC[min(attempt - 1, len(TIME_TEST_RESTORE_BACKOFF_SEC) - 1)])
    return False


# ZKTeco: direct pyzk connection via bio/zk_tool.py. Changes apply immediately.

def _zk_preflight(add_log) -> bool:
    add_log("connect", "Connecting to biometric device...", "pending")
    try:
        with Managed() as conn:
            device_time = conn.get_time()
    except Exception as e:
        add_log("error", f"Could not connect to the ZKTeco machine: {e}. "
                         "Nothing was changed on the machine.", "error")
        return False
    add_log("connect", f"Connected. Original device time: {device_time}", "success")
    return True


def _zk_set(add_log, target_dt: datetime) -> bool:
    add_log("set", f"Setting device time to: {target_dt}", "pending")
    try:
        with Managed() as conn:
            conn.set_time(target_dt)
            device_time = conn.get_time()
    except Exception as e:
        add_log("error", f"Setting the time failed: {e}. It may have been applied, "
                         "so restoring to be safe.", "error")
        return False
    if abs((device_time - target_dt).total_seconds()) > TIME_TEST_TOLERANCE_SEC:
        add_log("error", f"The machine reports {device_time} after the change. "
                         "Restoring to be safe.", "error")
        return False
    add_log("set", "Time set successfully on the machine.", "success")
    return True


def _zk_restore(add_log) -> bool:
    for attempt in range(1, TIME_TEST_RESTORE_ATTEMPTS + 1):
        add_log("restore", f"Restoring device time to the correct time "
                           f"(attempt {attempt}/{TIME_TEST_RESTORE_ATTEMPTS})...", "pending")
        try:
            # This server's clock is NTP-synced; "original + elapsed" would carry
            # over any error the machine already had.
            with Managed() as conn:
                conn.set_time(datetime.now())
                drift = (conn.get_time() - datetime.now()).total_seconds()
            if abs(drift) <= TIME_TEST_TOLERANCE_SEC:
                add_log("restore", f"Machine time restored successfully ({drift:+.0f}s from "
                                   "correct time). Test complete!", "success")
                return True
            add_log("restore", f"Machine clock is still off by {drift:+.0f}s.", "warning")
        except Exception as e:
            add_log("restore", f"Restore attempt failed: {e}", "warning")
        if attempt < TIME_TEST_RESTORE_ATTEMPTS:
            time.sleep(TIME_TEST_RESTORE_BACKOFF_SEC[min(attempt - 1, len(TIME_TEST_RESTORE_BACKOFF_SEC) - 1)])
    return False


TIME_TEST_MACHINES = {
    "zk": {"preflight": _zk_preflight, "set": _zk_set, "restore": _zk_restore},
    "rs9n": {"preflight": _rs9n_preflight, "set": _rs9n_set, "restore": _rs9n_restore},
}


def _restore_failed_message(machine: str) -> str:
    return (f"COULD NOT RESTORE the {machine} clock, so it may still be on the test time and "
            "every punch will be timestamped wrongly. Use 'Sync clock now' once the machine "
            "is reachable; the server also retries on its next start.")


def perform_time_test(task_id: str, machine: str, target_dt: datetime, wait_time_sec: int):
    """Background task: set the clock, wait, restore. Holds _time_test_lock."""
    add_log = _time_test_logger(task_id)
    impl = TIME_TEST_MACHINES[machine]
    outcome = "failed"
    clock_touched = False
    set_ok = False
    try:
        if not impl["preflight"](add_log):
            outcome = "aborted"
            return
        try:
            _save_time_test_state({"machine": machine, "task_id": task_id,
                                   "target": str(target_dt),
                                   "started_at": datetime.now().isoformat(sep=" ")})
        except OSError as e:
            add_log("error", f"Could not write the recovery marker ({e}); refusing to change "
                             "the clock without a way to restore it after a crash.", "error")
            outcome = "aborted"
            return

        clock_touched = True
        set_ok = impl["set"](add_log, target_dt)
        if set_ok:
            add_log("wait", f"Waiting for {wait_time_sec} seconds to let you test...", "pending")
            time.sleep(wait_time_sec)
            add_log("wait", "Wait time completed.", "success")
    except Exception as e:
        print(f"[Time Test {task_id[:8]}] unexpected error", flush=True)
        traceback.print_exc()
        add_log("error", f"Unexpected error: {e!r}. Restoring to be safe.", "error")
    finally:
        try:
            if clock_touched:
                try:
                    restored = impl["restore"](add_log)
                except Exception as e:
                    traceback.print_exc()
                    add_log("restore", f"Restore crashed: {e!r}", "warning")
                    restored = False
                if restored:
                    _clear_time_test_state()
                    outcome = "success" if set_ok else "set_failed_restored"
                else:
                    outcome = "restore_failed"
                    add_log("error", _restore_failed_message(machine), "error")
        finally:
            _finish_time_test_job(task_id, outcome)


def run_clock_restore(task_id: str, machines: List[str], reason: str):
    """Background job: put machines back on the correct time. Holds _time_test_lock."""
    add_log = _time_test_logger(task_id)
    outcome = "restore_failed"
    try:
        add_log("restore", reason, "info")
        failed = []
        for machine in machines:
            try:
                ok = TIME_TEST_MACHINES[machine]["restore"](add_log)
            except Exception as e:
                traceback.print_exc()
                add_log("restore", f"Restore crashed: {e!r}", "warning")
                ok = False
            if not ok:
                failed.append(machine)
                add_log("error", _restore_failed_message(machine), "error")
        if not failed:
            _clear_time_test_state()
            outcome = "restored"
    finally:
        _finish_time_test_job(task_id, outcome)


@app.on_event("startup")
def recover_interrupted_time_test():
    """A test was cut short (crash/restart) before its restore: restore now."""
    state = _load_time_test_state()
    if state is None or not _time_test_lock.acquire(blocking=False):
        return
    machine = state.get("machine")
    machines = [machine] if machine in TIME_TEST_MACHINES else list(TIME_TEST_MACHINES)
    task_id = _new_time_test_job(",".join(machines), "recovery")
    threading.Thread(
        target=run_clock_restore,
        args=(task_id, machines,
              "A time test was interrupted before it could restore the clock "
              "(server restart?). Restoring now..."),
        name="time-test-recovery", daemon=True,
    ).start()


def _time_test_busy_error():
    return HTTPException(status_code=409, detail={
        "message": "A time test or clock restore is already running.",
        "task_id": _time_test_active["task_id"],
    })


@app.post("/api/admin/test-time")
def run_time_test(
    req: schemas.TimeTestRequest,
    background_tasks: BackgroundTasks,
    admin_user: models.User = Depends(auth.check_permission([auth.ROLE_ADMIN, auth.ROLE_MANAGER]))
):
    machine = (req.machine or "zk").strip().lower()
    if machine not in TIME_TEST_MACHINES:
        raise HTTPException(status_code=400,
                            detail=f"machine must be 'zk' or 'rs9n'. Received: '{req.machine}'")

    if not TIME_TEST_MIN_WAIT_SEC <= req.wait_time_sec <= TIME_TEST_MAX_WAIT_SEC:
        raise HTTPException(status_code=400,
                            detail=f"wait_time_sec must be between {TIME_TEST_MIN_WAIT_SEC} and "
                                   f"{TIME_TEST_MAX_WAIT_SEC} seconds.")

    if req.use_today:
        target_date_str = datetime.now().date().strftime("%Y-%m-%d")
    else:
        if not req.target_date:
            raise HTTPException(status_code=400, detail="target_date is required when use_today is false")
        target_date_str = req.target_date

    try:
        try:
            # Try parsing with seconds first
            target_dt = datetime.strptime(f"{target_date_str} {req.target_time}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # Fallback to no seconds for backward compatibility
            target_dt = datetime.strptime(f"{target_date_str} {req.target_time}", "%Y-%m-%d %H:%M")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date or time format. Details: {e}")

    if not _time_test_lock.acquire(blocking=False):
        raise _time_test_busy_error()
    try:
        pending = _load_time_test_state()
        if pending is not None:
            raise HTTPException(status_code=409, detail={
                "message": "A previous time test did not restore the "
                           f"{pending.get('machine') or 'machine'} clock. Use 'Sync clock now' "
                           "first.",
                "task_id": None,
                "pending_restore": pending.get("machine") or "unknown",
            })
        task_id = _new_time_test_job(machine, "test")
        background_tasks.add_task(perform_time_test, task_id, machine, target_dt, req.wait_time_sec)
    except BaseException:
        _time_test_active["task_id"] = None
        _time_test_lock.release()
        raise

    return {
        "status": "success",
        "task_id": task_id,
        "machine": machine,
        "message": f"Time test initiated on '{machine}'. Setting time to {target_dt}, "
                   f"waiting {req.wait_time_sec}s, then restoring.",
    }


@app.post("/api/admin/test-time/restore")
def restore_machine_time(
    req: schemas.TimeRestoreRequest,
    background_tasks: BackgroundTasks,
    admin_user: models.User = Depends(auth.check_permission([auth.ROLE_ADMIN, auth.ROLE_MANAGER]))
):
    """Manually put a machine back on the correct time."""
    machine = (req.machine or "").strip().lower()
    if machine not in TIME_TEST_MACHINES:
        raise HTTPException(status_code=400,
                            detail=f"machine must be 'zk' or 'rs9n'. Received: '{req.machine}'")
    if not _time_test_lock.acquire(blocking=False):
        raise _time_test_busy_error()
    try:
        task_id = _new_time_test_job(machine, "restore")
        background_tasks.add_task(run_clock_restore, task_id, [machine],
                                  f"Syncing the {machine} clock to the correct time...")
    except BaseException:
        _time_test_active["task_id"] = None
        _time_test_lock.release()
        raise
    return {"status": "success", "task_id": task_id, "machine": machine}


@app.get("/api/admin/test-time/active")
def get_active_time_test(
    admin_user: models.User = Depends(auth.check_permission([auth.ROLE_ADMIN, auth.ROLE_MANAGER]))
):
    pending = _load_time_test_state()
    return {
        "task_id": _time_test_active["task_id"],
        "pending_restore": (pending.get("machine") or "unknown") if pending else None,
    }


@app.get("/api/admin/test-time/logs/{task_id}")
def get_time_test_logs(
    task_id: str,
    admin_user: models.User = Depends(auth.check_permission([auth.ROLE_ADMIN, auth.ROLE_MANAGER]))
):
    job = TIME_TEST_JOBS.get(task_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "logs": list(job["logs"]),
        "done": job["done"],
        "outcome": job["outcome"],
        "machine": job["machine"],
        "kind": job["kind"],
    }

@app.post("/api/admin/test-voice")
def run_voice_test(
    req: schemas.VoiceTestRequest,
    admin_user: models.User = Depends(auth.check_permission([auth.ROLE_ADMIN, auth.ROLE_MANAGER]))
):
    try:
        with Managed() as conn:
            result = op_test_voice(conn, req.index)
            return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to play voice: {e}")

# --- Serve Static Frontend ---
def _spa_index():
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    return {"message": "Task Management Server is running! Place your frontend in static/index.html to view."}

@app.get("/")
def read_root():
    return _spa_index()

@app.get("/signin")
def signin():
    return _spa_index()

@app.get("/kanban")
def spa_kanban():
    return _spa_index()

@app.get("/all-tasks")
def spa_all_tasks():
    return _spa_index()

@app.get("/dashboard")
def spa_dashboard():
    return _spa_index()

@app.get("/calendar")
def spa_calendar():
    return _spa_index()

@app.get("/projects")
def spa_projects():
    return _spa_index()

@app.get("/sprints")
def spa_sprints():
    return _spa_index()

@app.get("/users")
def spa_users():
    return _spa_index()

@app.get("/backups")
def spa_backups():
    return _spa_index()

@app.get("/test-time")
def spa_test_time():
    return _spa_index()

# Mount static files (PWA support, manifest, service worker)
app.mount("/static", StaticFiles(directory="static"), name="static")
# uploads/ is deliberately NOT mounted: attachments are served only through
# the authenticated /api/attachments/{id}, which applies the role checks.

@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    """Serve SPA shell for hard refresh on client-side routes."""
    if full_path.startswith(("api/", "static/", "uploads/")):
        raise HTTPException(status_code=404, detail="Not Found")
    return _spa_index()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=5001,
        reload=False,
    )
