from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator
from datetime import date, datetime
from typing import Annotated, List, Literal, Optional

# --- Input constraints ---
# Applied to request models only. Response models stay plain `str` so rows
# written before validation existed can still be read back.

Role = Literal["Admin", "Manager", "Developer", "Viewer"]
TaskStatus = Literal["Backlog", "Todo", "In Progress", "Review", "Testing", "Done"]
Priority = Literal["Low", "Medium", "High", "Critical"]
Category = Literal["Backend", "Frontend", "AI", "Database", "DevOps", "Testing", "Documentation", "Other"]
ProjectStatus = Literal["Active", "On Hold", "Closed"]
SprintStatus = Literal["Planned", "Active", "Completed"]


def _real_date(value: str) -> str:
    date.fromisoformat(value)   # rejects 2026-02-30 and similar
    return value


IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"), AfterValidator(_real_date)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
LongText = Annotated[str, StringConstraints(max_length=20000)]
Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=50,
                                            pattern=r"^[A-Za-z0-9_.@-]+$")]
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
SkillsText = Annotated[str, StringConstraints(max_length=500)]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Hours = Annotated[float, Field(ge=0, le=10000)]

# --- Label Schemas ---
class LabelBase(BaseModel):
    name: str
    color: str

class LabelCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    color: HexColor

class LabelResponse(LabelBase):
    id: int
    class Config:
        from_attributes = True

# --- User Schemas ---
class UserBase(BaseModel):
    username: str
    full_name: Optional[str] = None
    role: str = "Developer"
    skills: str = ""

class LoginRequest(BaseModel):
    # Deliberately unconstrained beyond length: existing accounts may predate
    # the password rules, and a login must never be rejected for its shape.
    username: Annotated[str, StringConstraints(max_length=100)]
    password: Annotated[str, StringConstraints(max_length=128)]

class UserCreate(BaseModel):
    username: Username
    password: Password
    full_name: Optional[ShortName] = None
    role: Role = "Developer"
    skills: SkillsText = ""

class UserUpdate(BaseModel):
    full_name: Optional[ShortName] = None
    role: Optional[Role] = None
    password: Optional[Password] = None
    skills: Optional[SkillsText] = None

class PasswordReset(BaseModel):
    old_password: Annotated[str, StringConstraints(max_length=128)]
    new_password: Password


class UserResponse(UserBase):
    id: int
    skill_list: List[str] = []
    class Config:
        from_attributes = True

    @classmethod
    def from_user(cls, user):
        data = cls.model_validate(user)
        data.skill_list = parse_skills(user.skills)
        return data

def parse_skills(skills_str: Optional[str]) -> List[str]:
    if not skills_str:
        return []
    return [s.strip() for s in skills_str.split(",") if s.strip()]

# --- Project Schemas ---
class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = ""
    status: str = "Active"
    lead_id: Optional[int] = None

class ProjectCreate(BaseModel):
    name: ShortName
    description: Optional[LongText] = ""
    status: ProjectStatus = "Active"
    lead_id: Optional[int] = None

class ProjectUpdate(BaseModel):
    name: Optional[ShortName] = None
    description: Optional[LongText] = None
    status: Optional[ProjectStatus] = None
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
    title: Title

class SubtaskUpdate(BaseModel):
    title: Optional[Title] = None
    is_completed: Optional[bool] = None
    sort_order: Optional[Annotated[int, Field(ge=0)]] = None

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
    task_ids: Annotated[List[int], Field(min_length=1, max_length=500)]
    action: Literal["assign", "status", "priority", "project", "sprint", "delete"]
    assignee_id: Optional[int] = None
    status: Optional[TaskStatus] = None
    priority: Optional[Priority] = None
    project_id: Optional[int] = None
    sprint_id: Optional[int] = None

# --- Sprint Schemas ---
class SprintBase(BaseModel):
    name: str
    project_id: Optional[int] = None
    start_date: str
    end_date: str
    goal: Optional[str] = ""
    status: str = "Planned"

class SprintCreate(BaseModel):
    name: ShortName
    project_id: Optional[int] = None
    start_date: IsoDate
    end_date: IsoDate
    goal: Optional[Annotated[str, StringConstraints(max_length=2000)]] = ""
    status: SprintStatus = "Planned"

    @model_validator(mode="after")
    def _dates_in_order(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self

class SprintUpdate(BaseModel):
    name: Optional[ShortName] = None
    project_id: Optional[int] = None
    start_date: Optional[IsoDate] = None
    end_date: Optional[IsoDate] = None
    goal: Optional[Annotated[str, StringConstraints(max_length=2000)]] = None
    status: Optional[SprintStatus] = None

class SprintResponse(SprintBase):
    id: int
    task_count: int = 0
    completed_count: int = 0
    created_at: datetime
    class Config:
        from_attributes = True

# --- Notification Schemas ---
class NotificationResponse(BaseModel):
    id: int
    task_id: Optional[int] = None
    type: str
    title: str
    message: str
    is_read: bool
    created_at: datetime
    class Config:
        from_attributes = True

# --- Comment Schemas ---
class CommentCreate(BaseModel):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]

class CommentResponse(BaseModel):
    id: int
    task_id: int
    content: str
    created_at: datetime
    user: Optional[UserResponse] = None   # None once the author is deleted
    class Config:
        from_attributes = True

# --- Attachment Schemas ---
class AttachmentResponse(BaseModel):
    id: int
    task_id: int
    filename: str
    file_size: int
    uploaded_at: datetime
    user: Optional[UserResponse] = None   # None once the author is deleted
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

class TaskCreate(BaseModel):
    title: Title
    description: Optional[LongText] = ""
    priority: Priority = "Medium"
    status: TaskStatus = "Backlog"
    category: Category = "Other"
    due_date: Optional[IsoDate] = None
    estimated_hours: Hours = 0.0
    actual_hours: Hours = 0.0
    assignee_id: Optional[int] = None
    project_id: Optional[int] = None
    sprint_id: Optional[int] = None
    label_ids: Optional[Annotated[List[int], Field(max_length=50)]] = []

class TaskUpdate(BaseModel):
    title: Optional[Title] = None
    description: Optional[LongText] = None
    priority: Optional[Priority] = None
    status: Optional[TaskStatus] = None
    category: Optional[Category] = None
    due_date: Optional[IsoDate] = None
    estimated_hours: Optional[Hours] = None
    actual_hours: Optional[Hours] = None
    assignee_id: Optional[int] = None
    project_id: Optional[int] = None
    sprint_id: Optional[int] = None
    label_ids: Optional[Annotated[List[int], Field(max_length=50)]] = None

class TaskStatusUpdate(BaseModel):
    status: TaskStatus

class TaskResponse(TaskBase):
    id: int
    assignee_id: Optional[int] = None
    project_id: Optional[int] = None
    sprint_id: Optional[int] = None
    completed_at: Optional[datetime] = None
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
    user: Optional[UserResponse] = None   # None once the author is deleted
    task_id: Optional[int] = None
    class Config:
        from_attributes = True

class TimeTestRequest(BaseModel):
    use_today: bool
    target_date: Optional[str] = None  # Expected format YYYY-MM-DD
    target_time: str                   # Expected format HH:MM
    wait_time_sec: int
    # Which biometric machine to run against: "zk" or "rs9n".
    # Defaults to "zk" so existing callers keep working unchanged.
    machine: str = "zk"

class VoiceTestRequest(BaseModel):
    index: int = 0

class TimeRestoreRequest(BaseModel):
    machine: str
