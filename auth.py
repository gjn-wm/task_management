import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import Request, HTTPException, Depends, status
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session
from database import get_db
import models

SECRET_KEY = "task-management-secret-key-for-jwt-token-management-2026"
ALGORITHM = "HS256"
COOKIE_NAME = "task_management_auth_token"

ROLE_ADMIN = "Admin"
ROLE_MANAGER = "Manager"
ROLE_DEVELOPER = "Developer"
ROLE_VIEWER = "Viewer"

ROLES_BULK = [ROLE_ADMIN, ROLE_MANAGER]
ROLES_PROJECT_MANAGE = [ROLE_ADMIN, ROLE_MANAGER]
ROLES_TASK_WRITE = [ROLE_ADMIN, ROLE_MANAGER, ROLE_DEVELOPER]
ROLES_SEE_ALL_TASKS = [ROLE_ADMIN, ROLE_MANAGER, ROLE_VIEWER]

oauth2_scheme = APIKeyCookie(name=COOKIE_NAME, auto_error=False)

def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    pwd_bytes = plain_password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    try:
        return bcrypt.checkpw(pwd_bytes, hashed_bytes)
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=7)  # 7 days expiration for convenience
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> models.User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        user = db.query(models.User).filter(models.User.username == username).first()
        return user
    except jwt.PyJWTError:
        return None

def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user

def check_permission(required_roles: list[str]):
    def dependency(user: models.User = Depends(get_current_user)):
        if user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action."
            )
        return user
    return dependency

def can_see_all_tasks(user: models.User) -> bool:
    return user.role in ROLES_SEE_ALL_TASKS

def can_bulk_operate(user: models.User) -> bool:
    return user.role in ROLES_BULK

def can_manage_projects(user: models.User) -> bool:
    return user.role in ROLES_PROJECT_MANAGE

def can_delete_tasks(user: models.User) -> bool:
    return user.role in [ROLE_ADMIN, ROLE_MANAGER]

def developer_can_access_task(user: models.User, task: models.Task) -> bool:
    if user.role != ROLE_DEVELOPER:
        return True
    return task.assignee_id is None or task.assignee_id == user.id
