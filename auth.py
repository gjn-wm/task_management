import threading
import time

import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import Request, HTTPException, Depends, status
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session

import config
from database import get_db
import models

SECRET_KEY = config.require("JWT_SECRET_KEY")
ALGORITHM = "HS256"
COOKIE_NAME = "task_management_auth_token"
TOKEN_TTL = timedelta(days=7)

ROLE_ADMIN = "Admin"
ROLE_MANAGER = "Manager"
ROLE_DEVELOPER = "Developer"
ROLE_VIEWER = "Viewer"
ALL_ROLES = (ROLE_ADMIN, ROLE_MANAGER, ROLE_DEVELOPER, ROLE_VIEWER)

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

def create_access_token(user: models.User, expires_delta: timedelta | None = None) -> str:
    # "ver" ties the token to user.token_version: bumping that (password or
    # role change, deletion) invalidates every session the user already has.
    expire = datetime.now(timezone.utc) + (expires_delta or TOKEN_TTL)
    payload = {"sub": str(user.id), "ver": user.token_version or 0, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def revoke_sessions(user: models.User):
    """Invalidate every token issued to this user so far. Caller commits."""
    user.token_version = (user.token_version or 0) + 1

def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> models.User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (jwt.PyJWTError, TypeError, ValueError):
        return None
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None or payload.get("ver") != (user.token_version or 0):
        return None
    return user

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


# --- Login throttling ---------------------------------------------------------
# In-memory, per process: enough to stop online password guessing against a
# single-instance deployment. Keyed by client IP and by username, so neither
# one attacker spraying usernames nor many IPs hammering one account get far.

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SEC = 15 * 60
_login_failures: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def client_ip(request: Request) -> str:
    # Behind cloudflared every request arrives from loopback; the real client
    # is in CF-Connecting-IP. Only trust that header from a loopback peer.
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def _recent(key: str, now: float) -> list[float]:
    stamps = [t for t in _login_failures.get(key, []) if now - t < LOGIN_WINDOW_SEC]
    if stamps:
        _login_failures[key] = stamps
    else:
        _login_failures.pop(key, None)
    return stamps


def login_retry_after(ip: str, username: str) -> int:
    """Seconds until another attempt is allowed; 0 if allowed now."""
    now = time.time()
    with _login_lock:
        waits = []
        for key in (f"ip:{ip}", f"user:{username.lower()}"):
            stamps = _recent(key, now)
            if len(stamps) >= LOGIN_MAX_FAILURES:
                waits.append(int(LOGIN_WINDOW_SEC - (now - stamps[0])) + 1)
        return max(waits, default=0)


def record_login_failure(ip: str, username: str):
    now = time.time()
    with _login_lock:
        for key in (f"ip:{ip}", f"user:{username.lower()}"):
            _login_failures.setdefault(key, []).append(now)


def clear_login_failures(ip: str, username: str):
    with _login_lock:
        _login_failures.pop(f"ip:{ip}", None)
        _login_failures.pop(f"user:{username.lower()}", None)
