"""The number of SQL statements must not grow with the number of rows.

Each endpoint is measured with a small and a large data set; any per-row
lazy load (the old N+1 pattern) makes the two counts differ.
"""
import pytest

from conftest import make_user


def _seed(admin, n):
    users = [make_user(f"u{n}_{i}") for i in range(3)]
    project = admin.post("/api/projects", json={"name": f"P{n}", "lead_id": users[0].id}).json()
    sprint = admin.post("/api/sprints", json={"name": f"S{n}", "start_date": "2026-01-01",
                                              "end_date": "2026-01-31", "project_id": project["id"]}).json()
    label = admin.post("/api/labels", json={"name": f"L{n}", "color": "#123456"}).json()
    for i in range(n):
        task = admin.post("/api/tasks", json={
            "title": f"t{i}", "assignee_id": users[i % 3].id, "project_id": project["id"],
            "sprint_id": sprint["id"], "label_ids": [label["id"]], "status": "Done" if i % 2 else "Todo",
        }).json()
        admin.post(f"/api/tasks/{task['id']}/subtasks", json={"title": "step"})


@pytest.mark.parametrize("path", ["/api/tasks", "/api/tasks/page?limit=50", "/api/projects",
                                  "/api/sprints", "/api/analytics"])
def test_query_count_does_not_grow_with_rows(admin, query_counter, path):
    _seed(admin, 3)
    with query_counter() as small:
        assert admin.get(path).status_code == 200
    _seed(admin, 15)
    with query_counter() as large:
        assert admin.get(path).status_code == 200
    assert large.value == small.value, f"{path}: {small.value} queries for few rows, {large.value} for many"
    assert large.value <= 12, f"{path}: {large.value} queries"
