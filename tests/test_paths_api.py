import os
import socket
import threading
import time

import httpx
import pytest
from waitress import serve

from app.main import create_app
from app.seed import seed_if_needed


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def api(tmp_path):
    db_path = str(tmp_path / "api.db")
    os.environ["DATABASE_PATH"] = db_path
    seed_if_needed(db_path)
    port = _free_port()
    server = threading.Thread(
        target=serve,
        args=(create_app(),),
        kwargs={"host": "127.0.0.1", "port": port, "threads": 4},
        daemon=True,
    )
    server.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            httpx.get(base + "/login", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    with httpx.Client(base_url=base) as client:
        resp = client.post("/api/login", json={"username": "admin", "password": "123456"})
        assert resp.status_code == 200
        yield client


def _node_id(client, code):
    items = client.get("/api/nodes").json()["items"]
    return int(next(n for n in items if n["node_code"] == code)["id"])


def test_star_api_and_duplicate_conflict(api):
    root = _node_id(api, "N-ODS-ORD")
    sink = _node_id(api, "N-ADS-DASH")
    body = {"root_node_id": root, "sink_node_id": sink, "landing": "snapshot"}
    first = api.post("/api/paths/star", json=body)
    assert first.status_code == 200, first.text
    item = first.json()["item"]
    assert item["path_length"] == 3
    assert item["landing_label"] == "不可变快照"

    second = api.post("/api/paths/star", json=body)
    assert second.status_code == 409
    assert "不可重复标星" in second.json()["error"]


def test_landing_mismatch_is_chinese_error(api):
    resp = api.post(
        "/api/paths/star",
        json={
            "root_node_id": _node_id(api, "N-ODS-ORD"),
            "sink_node_id": _node_id(api, "N-ADS-DASH"),
            "landing": "shadow",
        },
    )
    assert resp.status_code == 409
    assert "不可变快照" in resp.json()["error"]
    assert "不一致" in resp.json()["error"]


def test_no_published_path_is_chinese_error(api):
    resp = api.post(
        "/api/paths/star",
        json={
            "root_node_id": _node_id(api, "N-ADS-DASH"),
            "sink_node_id": _node_id(api, "N-ODS-ORD"),
            "landing": "snapshot",
        },
    )
    assert resp.status_code == 409
    assert "没有由已发布边构成的血缘路径" in resp.json()["error"]


def test_api_surfaces_chips_and_same_source_length(api):
    root = _node_id(api, "N-ODS-ORD")
    sink = _node_id(api, "N-ADS-DASH")
    api.post(
        "/api/paths/star",
        json={"root_node_id": root, "sink_node_id": sink, "landing": "snapshot"},
    )
    edges = api.get("/api/edges").json()["items"]
    chipped = [e for e in edges if e["path_chips"]]
    assert len(chipped) == 3
    assert all(c["path_length"] == 3 for e in chipped for c in e["path_chips"])

    nodes = api.get("/api/nodes").json()["items"]
    marked = [n for n in nodes if n["is_critical"]]
    assert len(marked) == 4
    assert all(n["path_lengths"] == [3] for n in marked)

    job = api.post("/api/impact/start", json={"root_node_id": root}).json()["job"]
    assert job["path_source"] == "不可变快照"
    assert job["path_length"] == 3
    detail = api.get(f"/api/impact/jobs/{job['id']}").json()
    assert detail["package"]["path_length"] == 3
    assert len(detail["package"]["path_rows"]) == 3


def test_orchestration_blocked_after_drift(api):
    root = _node_id(api, "N-ODS-ORD")
    sink = _node_id(api, "N-ADS-DASH")
    pkg = api.post(
        "/api/paths/star",
        json={"root_node_id": root, "sink_node_id": sink, "landing": "snapshot"},
    ).json()["item"]
    ok = api.post("/api/paths/orchestrate", json={"path_key": pkg["path_key"]})
    assert ok.status_code == 200
    assert ok.json()["status"] == "已编排"

    edge = next(e for e in api.get("/api/edges").json()["items"] if e["edge_code"] == "E-03")
    assert api.post(f"/api/edges/{edge['id']}/void", json={}).status_code == 200

    blocked = api.post("/api/paths/orchestrate", json={"path_key": pkg["path_key"]})
    assert blocked.status_code == 409
    assert "拦住" in blocked.json()["message"]

    impact = api.post("/api/impact/start", json={"root_node_id": root})
    assert impact.status_code == 409
    assert "已拦住" in impact.json()["error"]
