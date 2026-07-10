from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional

# --- Label Schemas ---
class LabelBase(BaseModel):
    name: str
    color: str

class LabelCreate(LabelBase):
    pass

class LabelResponse(LabelBase):
    id: int
    class Config:
        from_attributes = True

# --- User Schemas ---
class UserBase(BaseModel):
    username: str
    full_name: Optional[str] = None
    role: str = "Developer"

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None

class PasswordReset(BaseModel):
    old_password: str
    new_password: str


class UserResponse(UserBase):
    id: int
    class Config:
        from_attributes = True

# --- Project Schemas ---
class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = ""
    status: str = "Active"
    lead_id: Optional[int] = None

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    lead_id: Optional[int] = None

class ProjectResponse(ProjectBase):
    id: int
    lead: Optional[UserResponse] = None
    task_count: int = 0
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True

# --- Subtask Schemas ---
class SubtaskCreate(BaseModel):
    title: str

class SubtaskUpdate(BaseModel):
    title: Optional[str] = None
    is_completed: Optional[bool] = None
    sort_order: Optional[int] = None

class SubtaskResponse(BaseModel):
    id: int
    task_id: int
    title: str
    is_completed: bool
    sort_order: int
    created_at: datetime
    class Config:
        from_attributes = True

# --- Bulk Task Schemas ---
class BulkTaskAction(BaseModel):
    task_ids: List[int]
    action: str
    assignee_id: Optional[int] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    project_id: Optional[int] = None

# --- Comment Schemas ---
class CommentCreate(BaseModel):
    content: str

class CommentResponse(BaseModel):
    id: int
    task_id: int
    content: str
    created_at: datetime
    user: UserResponse
    class Config:
        from_attributes = True

# --- Attachment Schemas ---
class AttachmentResponse(BaseModel):
    id: int
    task_id: int
    filename: str
    file_path: str
    file_size: int
    uploaded_at: datetime
    user: UserResponse
    class Config:
        from_attributes = True

# --- Task Schemas ---
class TaskBase(BaseModel):
    title: str
    description: Optional[str] = ""
    priority: str = "Medium"
    status: str = "Backlog"
    category: str = "Other"
    due_date: Optional[str] = None
    estimated_hours: float = 0.0
    actual_hours: float = 0.0

class TaskCreate(TaskBase):
    assignee_id: Optional[int] = None
    project_id: Optional[int] = None
    label_ids: Optional[List[int]] = []

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None
    due_date: Optional[str] = None
    estimated_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    assignee_id: Optional[int] = None
    project_id: Optional[int] = None
    label_ids: Optional[List[int]] = None

class TaskResponse(TaskBase):
    id: int
    assignee_id: Optional[int] = None
    project_id: Optional[int] = None
    assignee: Optional[UserResponse] = None
    project: Optional[ProjectResponse] = None
    labels: List[LabelResponse] = []
    subtasks: List[SubtaskResponse] = []
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True

class TaskDetailResponse(TaskResponse):
    comments: List[CommentResponse] = []
    attachments: List[AttachmentResponse] = []
    class Config:
        from_attributes = True

# --- Activity Log Schemas ---
class ActivityLogResponse(BaseModel):
    id: int
    action: str
    details: Optional[str] = None
    timestamp: datetime
    user: UserResponse
    task_id: Optional[int] = None
    class Config:
        from_attributes = True
