import importlib
import math
import sys
import time

import pytest
from fastapi.testclient import TestClient

from conftest import MAGNITUDES


def load_app(home, monkeypatch, token=None):
    monkeypatch.setenv("ALLPORTALS_HOME", str(home))
    if token:
        monkeypatch.setenv("ALLPORTALS_TOKEN", token)
    else:
        monkeypatch.delenv("ALLPORTALS_TOKEN", raising=False)
    sys.modules.pop("app", None)
    return importlib.import_module("app")


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    return load_app(tmp_path, monkeypatch)


@pytest.fixture
def client(app_module):
    return TestClient(app_module.app)


def ring_points():
    return [
        {"x": round(m * math.cos(0.7 * i + 0.3)), "z": round(m * math.sin(0.7 * i + 0.3))}
        for i, m in enumerate(MAGNITUDES)
    ]


def wait_until_planned(client, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get("/api/state").json()
        if not state["solving"]:
            return state
        time.sleep(0.1)
    raise AssertionError("route plan never finished")


def start_run(client, names=("Syd", "Friend")):
    for name in names:
        assert client.post("/api/players", json={"name": name}).status_code == 200
    for point in ring_points():
        response = client.post("/api/ring-stronghold", json={**point, "player": names[0]})
        assert response.status_code == 200
    return wait_until_planned(client)


def test_empty_state(client):
    state = client.get("/api/state").json()
    assert state["players"] == [] and state["completed_count"] == "0 / 128"
    assert state["mode"].startswith("Pre-TSP") and not state["can_undo"]


def test_joining_is_idempotent_and_validated(client):
    assert client.post("/api/players", json={"name": "Syd"}).status_code == 200
    again = client.post("/api/players", json={"name": "syd"}).json()
    assert [p["name"] for p in again["players"]] == ["Syd"]
    assert client.post("/api/players", json={"name": "<script>"}).status_code == 409
    assert client.post("/api/players", json={"name": ""}).status_code == 422
    assert client.post("/api/players", json={"name": "A", "extra": 1}).status_code == 422


def test_full_two_player_run(client):
    state = start_run(client)
    assert state["is_solved"] and state["mode"] == "Active (TSP Solved)"
    assert state["completed_count"] == "8 / 128"
    stops = [p["route_remaining"] for p in state["players"]]
    assert sum(stops) == 120 and all(40 <= n <= 80 for n in stops)  # balanced by distance, not count
    targets = [p["target"]["index"] for p in state["players"]]
    assert targets[0] != targets[1]
    assert client.get(state["image_url"]).status_code == 200


def test_ring_endpoint_returns_before_the_plan_is_ready(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "solve_routes", lambda *a, **k: (time.sleep(1.5), [[], []])[1])
    for name in ("Syd", "Friend"):
        client.post("/api/players", json={"name": name})
    points = ring_points()
    for point in points[:-1]:
        client.post("/api/ring-stronghold", json=point)
    started = time.time()
    last = client.post("/api/ring-stronghold", json=points[-1]).json()
    assert time.time() - started < 1.0  # did not wait for the solver
    assert last["solving"] and last["mode"] == "Solving route"
    # ...and the dashboard stays responsive while it runs.
    started = time.time()
    assert client.get("/api/state").json()["solving"]
    assert time.time() - started < 0.5
    assert client.post("/api/players", json={"name": "Third"}).status_code == 200


def test_complete_is_idempotent_over_http(client):
    state = start_run(client)
    index = state["players"][0]["target"]["index"]
    first = client.post("/api/complete", json={"player": "Syd", "index": index}).json()
    second = client.post("/api/complete", json={"player": "Friend", "index": index}).json()
    assert first["completed_count"] == second["completed_count"] == "9 / 128"
    assert len([e for e in second["log"] if e["action"] == "complete"]) == 1


def test_skip_undo_and_log(client):
    state = start_run(client)
    index = state["players"][1]["target"]["index"]
    skipped = client.post("/api/skip", json={"player": "Friend", "index": index}).json()
    assert skipped["completed_count"] == "8 / 128" and skipped["can_undo"]
    undone = client.post("/api/undo", json={"player": "Syd"}).json()
    assert undone["log"][0]["action"] == "undo" and "Friend" in undone["log"][0]["detail"]
    wait_until_planned(client)


def test_errors(client):
    start_run(client)
    assert client.post("/api/complete", json={"player": "Ghost"}).status_code == 404
    assert client.post("/api/ring-stronghold", json={"x": 3500, "z": 0}).status_code == 409
    assert client.post("/api/ring-stronghold", json={"x": 2048, "z": 0}).status_code == 409  # ring 1 logged
    assert client.post("/api/count", json={"count": 500}).status_code == 422
    assert client.post("/api/complete", json={"player": "Syd", "index": 500}).status_code == 422
    assert client.post("/api/complete", json={"player": "Syd", "index": "x"}).status_code == 422


def test_reset_spawn_and_replan(client):
    start_run(client)
    state = client.post("/api/reset-spawn", json={"player": "Syd", "x": -15000, "z": 15000}).json()
    assert state["solving"]
    state = wait_until_planned(client)
    assert state["players"][0]["position"] == {"x": -15000, "z": 15000}
    assert client.post("/api/reoptimize", json={}).json()["solving"]
    wait_until_planned(client)


def test_count_edit(client):
    start_run(client)
    assert client.post("/api/count", json={"count": 40, "player": "Syd"}).json()["completed_count"] == "40 / 128"


def test_solver_failure_falls_back_to_nearest(client, app_module, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no solution")
    monkeypatch.setattr(app_module, "solve_routes", boom)
    state = start_run(client)
    assert not state["solving"] and "no solution" in state["solver_error"]
    assert all(p["target"] is not None for p in state["players"])
    assert "fallback" in state["mode"]


def test_run_survives_a_restart(tmp_path, monkeypatch):
    first = TestClient(load_app(tmp_path, monkeypatch).app)
    state = start_run(first)
    index = state["players"][0]["target"]["index"]
    before = first.post("/api/complete", json={"player": "Syd", "index": index}).json()
    wait_until_planned(first)

    second = TestClient(load_app(tmp_path, monkeypatch).app)
    after = wait_until_planned(second)
    assert after["completed_count"] == before["completed_count"] == "9 / 128"
    assert [p["name"] for p in after["players"]] == ["Syd", "Friend"]
    assert after["is_solved"] and after["can_undo"]
    assert len(after["log"]) == len(before["log"])


def test_corrupt_save_is_set_aside(tmp_path, monkeypatch, capsys):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "state.json").write_text("{not json")
    client = TestClient(load_app(tmp_path, monkeypatch).app)
    assert client.get("/api/state").json()["players"] == []
    assert (tmp_path / "data" / "state.json.bak").exists()
    assert "started fresh" in capsys.readouterr().out


def test_saved_run_is_not_served_publicly(client):
    start_run(client)
    assert client.get("/static/state.json").status_code == 404
    assert client.get("/static/AllPortals.py").status_code == 404


def test_token_protection(tmp_path, monkeypatch):
    client = TestClient(load_app(tmp_path, monkeypatch, token="s3cret").app)
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state?token=wrong").status_code == 401
    ok = client.get("/api/state?token=s3cret")
    assert ok.status_code == 200 and "ap_token" in ok.headers["set-cookie"]
    assert client.get("/api/state").status_code == 200  # the cookie remembers it
    assert TestClient(client.app).get("/api/state", headers={"x-allportals-token": "s3cret"}).status_code == 200
    assert client.get("/").status_code == 200


def test_index_page_is_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "AllPortals" in page.text


def test_overlay_page_is_served(client):
    page = client.get("/overlay")
    assert page.status_code == 200 and "/api/state" in page.text
