import shutil
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    s = Settings(runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "index.sqlite")
    with TestClient(create_app(s)) as c:
        yield c


def test_health_and_config(client):
    assert client.get("/api/health").json()["level"] == "L1-simulation"
    cfg = client.get("/api/config").json()
    assert cfg["task"]["target_count"] == 3
    assert "JOINT_LAG" in cfg["fault_types"]


def test_live_flow_over_rest_and_ws(client):
    run_id = client.post("/api/runs", json={"mode": "live", "seed": 1}).json()["run_id"]
    with client.websocket_connect("/ws/twin") as ws:
        first = ws.receive_json()
        assert first["type"] == "twin_state"
        for i in range(1, 15):
            ws.send_json(
                {
                    "type": "control",
                    "data": {
                        "timestamp": i * 0.05,
                        "device": "keyboard",
                        "sequence": i,
                        "boom": 1.0,
                    },
                }
            )
            time.sleep(0.05)
        snap = ws.receive_json()["data"]
        assert snap["run_id"] == run_id and snap["mode"] == "live"
    state = client.get("/api/twin/state").json()
    assert state["joint_states"]["boom_joint"]["position"] > 0.6  # moved up from 0.6
    r = client.post("/api/faults", json={"type": "JOINT_LAG", "target": "bucket_joint"})
    assert r.status_code == 200 and r.json()["kind"] == "fault"
    bad = client.post("/api/faults", json={"type": "JOINT_LAG", "target": "imu.roll"})
    assert bad.status_code == 422
    assert client.post("/api/estop").json()["estop"]
    report = client.post("/api/runs/stop").json()["report"]
    assert report["run_id"] == run_id and "score" in report
    assert client.get(f"/api/runs/{run_id}/report").json()["faults"]
    assert any(r["run_id"] == run_id for r in client.get("/api/runs").json())


def test_replay_endpoints(client, recorded_run):
    eng = client.app.state.engine
    shutil.copytree(recorded_run, eng.settings.runs_dir / recorded_run.name)
    rid = recorded_run.name
    assert client.post("/api/runs", json={"mode": "replay", "run_id": rid}).status_code == 200
    assert client.post("/api/replay/speed", json={"speed": 4}).status_code == 200
    assert client.post("/api/replay/seek", json={"t": 10}).status_code == 200
    client.post("/api/replay/play")
    time.sleep(0.3)
    snap = client.get("/api/twin/state").json()
    assert snap["mode"] == "replay" and snap["replay"]["cursor"] >= 10
    series = client.get(
        f"/api/runs/{rid}/series", params={"keys": "bucket_joint.command_error", "t_to": 30}
    ).json()
    assert series["series"]["bucket_joint.command_error"]["unit"] == "rad/s"
    assert (
        client.post(
            "/api/control", json={"timestamp": 0, "device": "api", "sequence": 1}
        ).status_code
        == 409
    )
    assert client.post("/api/runs", json={"mode": "replay", "run_id": "nope"}).status_code == 404
