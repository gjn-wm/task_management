import os
import shutil
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Response, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

import models
import schemas
import auth
from database import engine, get_db, Base
from upgrade_db import run_upgrade

# Create directories
os.makedirs("uploads", exist_ok=True)
os.makedirs("backups", exist_ok=True)

# Initialize database
Base.metadata.create_all(bind=engine)
run_upgrade()

app = FastAPI(title="Task Management API")

# Configure CORS for local development if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup event to create default admin user if none exists
@app.on_event("startup")
def create_initial_admin():
    db = next(get_db())
    try:
        admin_exists = db.query(models.User).filter(models.User.role == "Admin").first()
        if not admin_exists:
            hashed_pw = auth.hash_password("admin123")
            admin_user = models.User(
                username="admin",
                password_hash=hashed_pw,
                role="Admin",    
                full_name="System Administrator"
            )
            db.add(admin_user)
            db.commit()
            print("Initial admin user created: admin / admin123")
            
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
@app.post("/api/auth/login")
def login(response: Response, form_data: schemas.UserCreate, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect username or password"
        )
    
    # Create token
    access_token = auth.create_access_token(data={"sub": user.username})
    
    # Set cookies
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=access_token,
        httponly=True,
        max_age=604800, # 7 days
        samesite="lax",
        secure=False  # Set to True in production with HTTPS
    )
    return {"status": "success", "username": user.username, "role": user.role, "full_name": user.full_name}

@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME)
    return {"status": "success", "message": "Logged out"}

@app.get("/api/auth/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    return serialize_user(current_user)

@app.post("/api/auth/reset-password")
def reset_password(
    data: schemas.PasswordReset,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if current_user.id == 0 or current_user.username == "guest":
        raise HTTPException(status_code=400, detail="Guest users cannot reset passwords")
    
    if not auth.verify_password(data.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect old password")
        
    current_user.password_hash = auth.hash_password(data.new_password)
    db.commit()
    log_activity(db, current_user.id, "Password Reset", f"Changed password for {current_user.username}")
    return {"status": "success", "message": "Password changed successfully"}


# --- User Management Routes ---
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
        
    if user_in.full_name is not None:
        user.full_name = user_in.full_name
    if user_in.role is not None:
        user.role = user_in.role
    if user_in.skills is not None:
        user.skills = user_in.skills
    if user_in.password:
        user.password_hash = auth.hash_password(user_in.password)
        
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
        
    username = user.username
    db.delete(user)
    db.commit()
    
    log_activity(db, admin_user.id, "Delete User", f"Deleted user {username}")
    return {"status": "success", "message": f"User {username} deleted"}

# --- Task Routes ---
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
    status_update: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.check_permission(auth.ROLES_TASK_WRITE))
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not auth.developer_can_access_task(current_user, task):
        raise HTTPException(status_code=403, detail="You can only modify tasks assigned to you or unassigned tasks.")
        
    new_status = status_update.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="Status is required")
        
    old_status = task.status
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

    tasks = db.query(models.Task).filter(models.Task.id.in_(bulk_in.task_ids)).all()
    if len(tasks) != len(bulk_in.task_ids):
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
@app.post("/api/tasks/{task_id}/attachments", response_model=schemas.AttachmentResponse)
async def upload_attachment(
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
        
    # Generate path
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    clean_filename = f"{timestamp}_{file.filename.replace(' ', '_')}"
    file_path = os.path.join("uploads", clean_filename)
    
    # Save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # File size
    file_size = os.path.getsize(file_path)
    
    # DB entry
    attachment = models.Attachment(
        task_id=task_id,
        user_id=current_user.id,
        filename=file.filename,
        file_path=file_path,
        file_size=file_size
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    
    log_activity(db, current_user.id, "Upload File", f"Uploaded file: {file.filename}", task.id)
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
            
    if not os.path.exists(attachment.file_path):
        raise HTTPException(status_code=404, detail="File not found on server")
        
    return FileResponse(
        path=attachment.file_path,
        filename=attachment.filename,
        media_type="application/octet-stream"
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
    
    try:
        # First commit anything pending
        db.commit()
        # Backup Postgres database using pg_dump via docker
        import subprocess
        with open(backup_path, "w", encoding="utf-8") as f:
            cmd = ["docker", "exec", "-i", "-e", "PGPASSWORD=gajendra", "postgres18", "pg_dump", "-U", "admin", "gjn_db"]
            subprocess.run(cmd, stdout=f, check=True)
        log_activity(db, admin_user.id, "Database Backup", f"Created database backup: {backup_filename}")
        return {"status": "success", "filename": backup_filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup failed: {str(e)}")

@app.get("/api/backups/{filename}")
def download_backup(
    filename: str,
    admin_user: models.User = Depends(auth.check_permission(["Admin"]))
):
    path = os.path.join("backups", filename)
    if not os.path.exists(path):
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
    path = os.path.join("backups", filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Backup file not found")
        
    try:
        os.remove(path)
        log_activity(db, admin_user.id, "Delete Backup", f"Deleted backup file: {filename}")
        return {"status": "success", "message": f"Backup {filename} deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete backup: {str(e)}")

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

# Mount static files (PWA support, manifest, service worker)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

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
        host="0.0.0.0",
        port=5001,
        reload=False,
    )
