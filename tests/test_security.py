import os
import re
import shutil

import server
from conftest import client_for, make_user


def test_security_headers(anon):
    page = anon.get("/")
    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-cache"
    assert anon.get("/health").headers.get("access-control-allow-origin") is None


def test_api_is_not_cached_and_static_revalidates(admin):
    assert admin.get("/api/tasks").headers["cache-control"] == "no-store"
    static = admin.get("/static/service-worker.js").headers
    assert static["cache-control"] == "no-cache"
    assert static["cdn-cache-control"] == "no-store"
    assert static["cloudflare-cdn-cache-control"] == "no-store"


def test_cross_site_writes_are_refused(admin):
    body = {"name": "x", "color": "#112233"}
    assert admin.post("/api/labels", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert admin.post("/api/labels", json=body, headers={"Origin": "http://testserver"}).status_code == 200


def _upload(client, task_id, name="note.txt", content=b"hello", ctype="text/plain"):
    return client.post(f"/api/tasks/{task_id}/attachments", files={"file": (name, content, ctype)})


def test_attachments_are_private_downloads(admin, anon):
    task = admin.post("/api/tasks", json={"title": "t"}).json()
    att = _upload(admin, task["id"], "../../evil page.html", b"<script>alert(1)</script>", "text/html").json()
    assert att["filename"] == "evil page.html"
    assert "file_path" not in att
    stored = os.listdir(server.UPLOAD_DIR)
    assert len(stored) == 1 and stored[0].endswith(".html") and len(stored[0]) == 37

    r = admin.get(f"/api/attachments/{att['id']}")
    assert r.status_code == 200 and r.content == b"<script>alert(1)</script>"
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")

    assert anon.get(f"/api/attachments/{att['id']}").status_code == 401
    assert anon.get(f"/uploads/{stored[0]}").status_code == 404


def test_developer_cannot_download_other_peoples_attachments(admin):
    make_user("dev_a"); other = make_user("dev_b")
    task = admin.post("/api/tasks", json={"title": "t", "assignee_id": other.id}).json()
    att = _upload(admin, task["id"]).json()
    assert client_for("dev_a").get(f"/api/attachments/{att['id']}").status_code == 403


def test_upload_size_limit(admin, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 1024)
    task = admin.post("/api/tasks", json={"title": "t"}).json()
    assert _upload(admin, task["id"], content=b"x" * 2048).status_code == 413
    assert os.listdir(server.UPLOAD_DIR) == []


def test_backup_names_are_validated(admin):
    assert admin.get("/api/backups/server.py").status_code == 400
    assert admin.get("/api/backups/..%2Fserver.py").status_code in (400, 404)
    assert admin.get("/api/backups/database_2026-01-01_00-00-00.sql").status_code == 404


def test_backups_are_admin_only(manager):
    assert manager.get("/api/backups").status_code == 403


def test_service_worker_is_served_from_root(anon):
    r = anon.get("/service-worker.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")
    assert r.headers["cache-control"] == "no-cache"
    assert "networkFirst" in r.text


def test_asset_urls_carry_a_content_version(anon, monkeypatch, tmp_path):
    html = anon.get("/").text
    refs = re.findall(r"""["'](/static/(?:js|css)/[\w.-]+|/service-worker\.js)(\?v=[0-9a-f]{12})?["']""", html)
    assert refs and all(version for _, version in refs), refs
    css = anon.get("/static/css/app.css")
    assert css.headers["content-type"].startswith("text/css")
    imports = re.findall(r"@import url\(['\"]([^'\"]+)['\"]\)", css.text)
    assert imports and all(re.search(r"\.css\?v=[0-9a-f]{12}$", i) for i in imports), imports

    # Editing any partial changes the app.css version, so the new CSS reaches clients.
    static = tmp_path / "static"
    shutil.copytree(server.STATIC_DIR, static)
    monkeypatch.setattr(server, "STATIC_DIR", str(static))
    before = re.search(r"/static/css/app\.css\?v=(\w+)", anon.get("/").text).group(1)
    partial = next(p for p in (static / "css").iterdir() if p.name != "app.css")
    partial.write_text(partial.read_text() + "\n/* changed */\n")
    after = re.search(r"/static/css/app\.css\?v=(\w+)", anon.get("/").text).group(1)
    assert before != after
