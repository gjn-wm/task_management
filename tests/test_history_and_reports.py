import os
from datetime import datetime, timedelta, timezone

import models
import server
from conftest import client_for, make_user


def test_deleting_a_user_keeps_their_work(admin, db):
    user = make_user("dev_a")
    dev = client_for("dev_a")
    task = dev.post("/api/tasks", json={"title": "dev task", "assignee_id": user.id}).json()
    dev.post(f"/api/tasks/{task['id']}/comments", json={"content": "hello"})

    assert admin.delete(f"/api/users/{user.id}").status_code == 200

    detail = admin.get(f"/api/tasks/{task['id']}").json()
    assert len(detail["comments"]) == 1 and detail["comments"][0]["user"] is None
    assert db.query(models.ActivityLog).filter(models.ActivityLog.user_id.is_(None)).count() >= 2
    feed = admin.get("/api/analytics").json()["recent_logs"]
    assert any(entry["username"] == "Deleted user" for entry in feed)


def test_deleting_a_task_keeps_history_and_removes_files(admin, db):
    task = admin.post("/api/tasks", json={"title": "doomed"}).json()
    admin.post(f"/api/tasks/{task['id']}/attachments", files={"file": ("a.txt", b"data", "text/plain")})
    assert len(os.listdir(server.UPLOAD_DIR)) == 1

    assert admin.delete(f"/api/tasks/{task['id']}").status_code == 200

    assert os.listdir(server.UPLOAD_DIR) == []
    kept = db.query(models.ActivityLog).filter(models.ActivityLog.details == "Created task: doomed").one()
    assert kept.task_id is None


def test_due_alerts_are_created_by_the_badge_poll(admin, db):
    today = datetime.now().strftime("%Y-%m-%d")
    task = admin.post("/api/tasks", json={"title": "due now", "due_date": today}).json()
    assert admin.get("/api/notifications/unread-count").json()["count"] == 1
    note = db.query(models.Notification).filter_by(task_id=task["id"]).one()
    assert note.type == "due_today"
    server._last_due_sync.clear()
    admin.get("/api/notifications/unread-count")                     # no duplicate on the next poll
    assert db.query(models.Notification).filter_by(task_id=task["id"]).count() == 1


def test_completion_in_the_early_local_morning_counts_on_that_day(admin, db):
    sprint = admin.post("/api/sprints", json={"name": "s", "start_date": "2026-01-01",
                                              "end_date": "2099-12-31"}).json()
    task = admin.post("/api/tasks", json={"title": "early bird", "status": "Done", "sprint_id": sprint["id"]}).json()
    # 00:30 local today. In a UTC+ zone (IST) that is still *yesterday* in UTC.
    local_early = datetime.now().replace(hour=0, minute=30, second=0, microsecond=0)
    stored = local_early.astimezone(timezone.utc).replace(tzinfo=None)
    db.query(models.Task).filter_by(id=task["id"]).update({"completed_at": stored})
    db.commit()

    burndown = admin.get(f"/api/sprints/{sprint['id']}/burndown").json()
    remaining = dict(zip(burndown["dates"], burndown["actual_remaining"]))
    today = datetime.now().date()
    assert remaining[today.isoformat()] == 0
    assert remaining[(today - timedelta(days=1)).isoformat()] == 1
    assert admin.get("/api/analytics").json()["summary"]["completed_this_week"] == 1


def test_project_and_sprint_counts(admin):
    project = admin.post("/api/projects", json={"name": "P"}).json()
    sprint = admin.post("/api/sprints", json={"name": "S", "start_date": "2026-01-01", "end_date": "2026-01-31"}).json()
    admin.post("/api/tasks", json={"title": "a", "project_id": project["id"], "sprint_id": sprint["id"], "status": "Done"})
    admin.post("/api/tasks", json={"title": "b", "project_id": project["id"], "sprint_id": sprint["id"]})
    assert admin.get("/api/projects").json()[0]["task_count"] == 2
    listed = admin.get("/api/sprints").json()[0]
    assert (listed["task_count"], listed["completed_count"]) == (2, 1)
