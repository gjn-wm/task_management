import jwt

import auth
import server
from conftest import client_for, make_user


def test_login_and_me(anon):
    make_user("alice", role="Manager")
    r = anon.post("/api/auth/login", json={"username": "alice", "password": "Password-123"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert anon.get("/api/auth/me").json()["username"] == "alice"


def test_wrong_password_is_rejected(anon):
    make_user("alice")
    r = anon.post("/api/auth/login", json={"username": "alice", "password": "nope"})
    assert r.status_code == 400
    assert anon.get("/api/auth/me").status_code == 401


def test_login_is_throttled_after_five_failures(anon):
    make_user("alice")
    codes = [anon.post("/api/auth/login", json={"username": "alice", "password": "bad"}).status_code
             for _ in range(6)]
    assert codes == [400] * 5 + [429]
    r = anon.post("/api/auth/login", json={"username": "alice", "password": "Password-123"})
    assert r.status_code == 429 and r.headers.get("retry-after")


def test_password_change_revokes_other_sessions():
    make_user("alice")
    first, second = client_for("alice"), client_for("alice")
    r = first.post("/api/auth/reset-password", json={"old_password": "Password-123", "new_password": "NewPass-456"})
    assert r.status_code == 200
    assert first.get("/api/auth/me").status_code == 200      # this session is re-issued
    assert second.get("/api/auth/me").status_code == 401     # every other one is revoked


def test_role_change_revokes_sessions(admin):
    user = make_user("bob")
    bob = client_for("bob")
    assert admin.put(f"/api/users/{user.id}", json={"role": "Viewer"}).status_code == 200
    assert bob.get("/api/auth/me").status_code == 401


def test_tokens_signed_with_another_key_or_old_format_are_rejected(anon):
    make_user("alice", role="Admin")
    forged = jwt.encode({"sub": "alice", "exp": 4102444800}, "some-other-signing-key-of-sufficient-length", algorithm="HS256")
    anon.cookies.set(auth.COOKIE_NAME, forged)
    assert anon.get("/api/auth/me").status_code == 401
    old_format = jwt.encode({"sub": "alice", "exp": 4102444800}, auth.SECRET_KEY, algorithm="HS256")
    anon.cookies.set(auth.COOKIE_NAME, old_format)
    assert anon.get("/api/auth/me").status_code == 401


def test_last_admin_cannot_be_demoted_or_deleted(admin, db):
    me = admin.get("/api/auth/me").json()
    assert admin.put(f"/api/users/{me['id']}", json={"role": "Developer"}).status_code == 400
    other = make_user("admin2", role="Admin")
    assert admin.delete(f"/api/users/{other.id}").status_code == 200
    assert admin.get("/api/auth/me").json()["role"] == "Admin"


def test_initial_admin_only_on_empty_users_table(db):
    import models
    server.create_initial_admin()
    assert db.query(models.User).filter_by(username="admin").count() == 1
    db.query(models.User).filter_by(username="admin").update({"role": "Developer"})
    db.commit()
    server.create_initial_admin()   # users exist: must not recreate an admin
    assert db.query(models.User).filter_by(role="Admin").count() == 0


def test_user_input_is_validated(admin):
    bad = [
        {"username": "x1", "password": "short"},
        {"username": "x2", "password": "Password-123", "role": "superuser"},
        {"username": "bad name<script>", "password": "Password-123"},
    ]
    for body in bad:
        assert admin.post("/api/users", json=body).status_code == 422, body
    assert admin.post("/api/users", json={"username": "ok_user", "password": "Password-123"}).status_code == 200


def test_only_admin_manages_users(developer, manager):
    for client in (developer, manager):
        assert client.post("/api/users", json={"username": "zz", "password": "Password-123"}).status_code == 403


def test_startup_lifespan_seeds_first_admin(db):
    import models
    from fastapi.testclient import TestClient
    with TestClient(server.app) as client:          # runs the lifespan startup
        r = client.post("/api/auth/login", json={"username": "admin", "password": "Initial-Admin-1"})
        assert r.status_code == 200 and r.json()["role"] == "Admin"
    assert db.query(models.User).count() == 1
