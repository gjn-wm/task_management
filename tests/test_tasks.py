from datetime import date, timedelta

from conftest import client_for, make_user


def test_task_input_is_validated(admin):
    for body in ({"title": "t", "status": "Nonsense"}, {"title": "t", "due_date": "2026-02-30"},
                 {"title": "   "}, {"title": "t", "estimated_hours": -1}):
        assert admin.post("/api/tasks", json=body).status_code == 422, body
    assert admin.post("/api/tasks", json={"title": "t", "assignee_id": 999999}).status_code == 400


def test_developer_sees_own_and_unassigned_only(admin):
    me = make_user("dev_a"); other = make_user("dev_b")
    admin.post("/api/tasks", json={"title": "mine", "assignee_id": me.id})
    admin.post("/api/tasks", json={"title": "theirs", "assignee_id": other.id})
    admin.post("/api/tasks", json={"title": "nobody"})
    dev = client_for("dev_a")
    assert sorted(t["title"] for t in dev.get("/api/tasks").json()) == ["mine", "nobody"]
    theirs = [t for t in admin.get("/api/tasks").json() if t["title"] == "theirs"][0]
    assert dev.get(f"/api/tasks/{theirs['id']}").status_code == 403


def test_developer_cannot_assign_to_others(developer):
    other = make_user("dev_b")
    assert developer.post("/api/tasks", json={"title": "x", "assignee_id": other.id}).status_code == 403
    assert developer.post("/api/tasks", json={"title": "x", "assignee_id": developer.user_id}).status_code == 200


def test_viewer_is_read_only(viewer):
    assert viewer.get("/api/tasks").status_code == 200
    assert viewer.post("/api/tasks", json={"title": "x"}).status_code == 403


def test_null_clears_fields_and_omitted_fields_stay(admin):
    user = make_user("dev_a")
    project = admin.post("/api/projects", json={"name": "P"}).json()
    task = admin.post("/api/tasks", json={"title": "t", "description": "keep", "due_date": "2026-10-20",
                                          "assignee_id": user.id, "project_id": project["id"]}).json()
    updated = admin.put(f"/api/tasks/{task['id']}", json={"assignee_id": None, "due_date": None,
                                                          "project_id": None}).json()
    assert updated["assignee_id"] is None and updated["due_date"] is None and updated["project_id"] is None
    assert updated["description"] == "keep" and updated["title"] == "t"
    r = admin.put(f"/api/tasks/{task['id']}", json={"title": None})
    assert r.status_code == 200 and r.json()["title"] == "t"


def test_status_change_sets_and_clears_completed_at(admin):
    task = admin.post("/api/tasks", json={"title": "t", "status": "Todo"}).json()
    done = admin.patch(f"/api/tasks/{task['id']}/status", json={"status": "Done"}).json()
    assert done["completed_at"] is not None
    reopened = admin.patch(f"/api/tasks/{task['id']}/status", json={"status": "Review"}).json()
    assert reopened["completed_at"] is None
    assert admin.patch(f"/api/tasks/{task['id']}/status", json={"status": "Bogus"}).status_code == 422


def test_search_is_case_insensitive_and_literal(admin):
    for title in ("Fix LOGIN page", "100% done", "100x done"):
        admin.post("/api/tasks", json={"title": title})
    assert [t["title"] for t in admin.get("/api/tasks", params={"search": "login"}).json()] == ["Fix LOGIN page"]
    assert [t["title"] for t in admin.get("/api/tasks", params={"search": "100%"}).json()] == ["100% done"]
    assert admin.get("/api/tasks", params={"search": "_"}).json() == []


def test_task_page_endpoint(admin):
    for i in range(25):
        admin.post("/api/tasks", json={"title": f"task {i:02d}"})
    first = admin.get("/api/tasks/page", params={"limit": 10, "offset": 0}).json()
    last = admin.get("/api/tasks/page", params={"limit": 10, "offset": 20}).json()
    assert first["total"] == 25 and len(first["items"]) == 10 and len(last["items"]) == 5
    assert first["items"][0]["title"] == "task 24"          # newest first
    ids = {t["id"] for t in first["items"]} | {t["id"] for t in last["items"]}
    assert len(ids) == 15                                    # no overlap between pages
    assert admin.get("/api/tasks/page", params={"limit": 1000}).status_code == 422


def test_task_page_quick_filters(admin):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    admin.post("/api/tasks", json={"title": "late", "status": "Todo", "due_date": yesterday})
    admin.post("/api/tasks", json={"title": "late but done", "status": "Done", "due_date": yesterday})
    admin.post("/api/tasks", json={"title": "upcoming", "status": "In Progress", "due_date": tomorrow})
    admin.post("/api/tasks", json={"title": "finished", "status": "Done"})
    titles = lambda **p: sorted(t["title"] for t in admin.get("/api/tasks/page", params=p).json()["items"])
    assert titles(open_only=True) == ["late", "upcoming"]
    assert titles(overdue=True) == ["late"]


def test_bulk_actions(admin):
    user = make_user("dev_a")
    ids = [admin.post("/api/tasks", json={"title": f"t{i}", "assignee_id": user.id}).json()["id"] for i in range(3)]
    assert admin.post("/api/tasks/bulk", json={"task_ids": ids, "action": "status"}).status_code == 400
    assert admin.post("/api/tasks/bulk", json={"task_ids": ids, "action": "explode"}).status_code == 422
    r = admin.post("/api/tasks/bulk", json={"task_ids": ids, "action": "assign", "assignee_id": None})
    assert r.status_code == 200 and r.json()["updated"] == 3
    assert all(t["assignee_id"] is None for t in admin.get("/api/tasks").json())


def test_bulk_is_admin_or_manager_only(developer):
    assert developer.post("/api/tasks/bulk", json={"task_ids": [1], "action": "delete"}).status_code == 403


def test_sprint_dates_must_be_ordered(admin):
    bad = {"name": "s", "start_date": "2026-10-10", "end_date": "2026-10-01"}
    assert admin.post("/api/sprints", json=bad).status_code == 422
    sprint = admin.post("/api/sprints", json={"name": "s", "start_date": "2026-10-01", "end_date": "2026-10-31"}).json()
    assert admin.put(f"/api/sprints/{sprint['id']}", json={"end_date": "2026-09-01"}).status_code == 400
