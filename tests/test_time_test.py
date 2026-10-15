"""Biometric clock test: the failsafes that keep a machine off the wrong time.

rs9n_call is replaced with a fake attendance API; no device is contacted.
"""
import os
from datetime import datetime, timedelta

import pytest

import server


class FakeRS9N:
    def __init__(self, set_results):
        self.set_results = list(set_results)
        self.calls = []

    def __call__(self, method, path, payload=None, timeout=120):
        self.calls.append((method, path, payload))
        if method == "GET":
            now = datetime.now()
            return {"data": {"device_id": "FAKE", "device_time": str(now.replace(microsecond=0)),
                             "server_time_at_last_contact": str(now - timedelta(seconds=2)),
                             "server_time_now": str(now), "drift_seconds": 0}}
        result = self.set_results.pop(0) if self.set_results else "ok"
        if isinstance(result, Exception):
            raise result
        if result == "unconfirmed":
            return {"confirmed": False, "message": "queued"}
        return {"confirmed": True, "return_code": "OK", "clock_after": {"drift_seconds": 0.0}}

    @property
    def set_payloads(self):
        return [p for m, _, p in self.calls if m == "POST"]


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(server.time, "sleep", lambda s: None)

    def install(*results):
        api = FakeRS9N(results)
        monkeypatch.setattr(server, "rs9n_call", api)
        return api
    return install


def run(admin):
    r = admin.post("/api/admin/test-time", json={"use_today": True, "target_time": "09:00:00",
                                                  "wait_time_sec": 5, "machine": "rs9n"})
    assert r.status_code == 200, r.text
    # TestClient runs background tasks before returning.
    return admin.get(f"/api/admin/test-time/logs/{r.json()['task_id']}").json()


def test_restore_is_stamped_by_the_clock_service(admin, fake):
    api = fake()
    job = run(admin)
    assert job["done"] and job["outcome"] == "success"
    assert api.set_payloads[0]["time"].endswith("09:00:00")
    assert "time" not in api.set_payloads[1]           # restore: no stale timestamp
    assert not os.path.exists(server.TIME_TEST_STATE_FILE)


@pytest.mark.parametrize("failure", ["unconfirmed", RuntimeError("timed out")])
def test_failed_set_is_still_restored(admin, fake, failure):
    api = fake(failure, "ok")
    job = run(admin)
    assert job["outcome"] == "set_failed_restored"
    assert len(api.set_payloads) == 2 and "time" not in api.set_payloads[1]


def test_failed_restore_leaves_marker_and_blocks_new_tests(admin, fake):
    fake("ok", "unconfirmed", "unconfirmed", "unconfirmed")
    job = run(admin)
    assert job["outcome"] == "restore_failed"
    assert os.path.exists(server.TIME_TEST_STATE_FILE)
    r = admin.post("/api/admin/test-time", json={"use_today": True, "target_time": "09:00:00",
                                                  "wait_time_sec": 5, "machine": "rs9n"})
    assert r.status_code == 409 and r.json()["detail"]["pending_restore"] == "rs9n"


def test_time_test_is_admin_only(manager):
    assert manager.get("/api/admin/test-time/active").status_code == 403
