"""Test setup: a real PostgreSQL database, wiped before every test.

Set TEST_DATABASE_URL to a database whose name ends in "_test" (it is created
if missing). The suite refuses any other name, so it can never truncate a real
database. Example:

    TEST_DATABASE_URL=postgresql://USER:PASS@localhost:5432/gjn_db_test pytest
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
if not TEST_DATABASE_URL:
    pytest.exit("TEST_DATABASE_URL is not set (see tests/conftest.py).", returncode=2)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

_url = make_url(TEST_DATABASE_URL)
if not (_url.database or "").endswith("_test"):
    pytest.exit(f"Refusing to run against database {_url.database!r}: its name must end in '_test'.",
                returncode=2)

# Create the database on first use.
_admin_engine = create_engine(_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
with _admin_engine.connect() as conn:
    if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": _url.database}).first():
        conn.execute(text(f'CREATE DATABASE "{_url.database}"'))
_admin_engine.dispose()

# Everything the app reads from the environment must be set before it is imported.
_tmp = tempfile.mkdtemp(prefix="task_itms_tests_")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["JWT_SECRET_KEY"] = "test-only-signing-key-0123456789abcdef"
os.environ["UPLOAD_DIR"] = os.path.join(_tmp, "uploads")
os.environ["BACKUP_DIR"] = os.path.join(_tmp, "backups")
os.environ["INITIAL_ADMIN_PASSWORD"] = "Initial-Admin-1"
os.environ["RS9N_API_BASE"] = "http://127.0.0.1:9"   # never reached: tests stub rs9n_call
os.environ["RS9N_API_TOKEN"] = "test-token"

import bcrypt  # noqa: E402

# Cost 4 instead of 12: same code path, ~100x faster password hashing.
_real_gensalt = bcrypt.gensalt
bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _real_gensalt(4, prefix)

import database  # noqa: E402
import models  # noqa: E402

# Fresh schema for the session; importing server then runs create_all + upgrade,
# exactly as production does at startup.
database.Base.metadata.drop_all(bind=database.engine)
import server  # noqa: E402
import auth  # noqa: E402

server.TIME_TEST_STATE_FILE = os.path.join(_tmp, "time_test_state.json")

from fastapi.testclient import TestClient  # noqa: E402

TABLES = ", ".join(t.name for t in reversed(database.Base.metadata.sorted_tables))


@pytest.fixture(autouse=True)
def clean_state():
    """Empty every table and reset in-memory state before each test."""
    with database.engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    auth._login_failures.clear()
    server._last_due_sync.clear()
    server.TIME_TEST_JOBS.clear()
    if server._time_test_lock.locked():
        server._time_test_lock.release()
    server._time_test_active["task_id"] = None
    if os.path.exists(server.TIME_TEST_STATE_FILE):
        os.remove(server.TIME_TEST_STATE_FILE)
    for d in (server.UPLOAD_DIR, server.BACKUP_DIR):
        for name in os.listdir(d):
            os.remove(os.path.join(d, name))
    yield


@pytest.fixture
def db():
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()


def make_user(username, role="Developer", password="Password-123", full_name=None):
    session = database.SessionLocal()
    try:
        user = models.User(username=username, role=role, full_name=full_name or username,
                           password_hash=auth.hash_password(password), skills="")
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
    finally:
        session.close()


def client_for(username, password="Password-123"):
    """A TestClient with its own cookie jar, logged in as `username`."""
    client = TestClient(server.app)
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def anon():
    return TestClient(server.app)


@pytest.fixture
def admin():
    make_user("admin1", role="Admin")
    return client_for("admin1")


@pytest.fixture
def manager():
    make_user("manager1", role="Manager")
    return client_for("manager1")


@pytest.fixture
def developer():
    user = make_user("dev1", role="Developer")
    client = client_for("dev1")
    client.user_id = user.id
    return client


@pytest.fixture
def viewer():
    make_user("viewer1", role="Viewer")
    return client_for("viewer1")


@pytest.fixture
def query_counter():
    """Counts SQL statements: `with query_counter() as n: ...; n.value`."""
    from contextlib import contextmanager
    from sqlalchemy import event

    class Counter:
        value = 0

    @contextmanager
    def counting():
        counter = Counter()

        def before(*_args, **_kwargs):
            counter.value += 1

        event.listen(database.engine, "before_cursor_execute", before)
        try:
            yield counter
        finally:
            event.remove(database.engine, "before_cursor_execute", before)

    return counting
