import pytest

from app.anomaly.service import AnomalyService
from app.core.config import Settings
from app.engine import Engine
from twinpilot_ai.training.fixtures import build_fixture_bundle
from twinpilot_sim.schema import FaultSpec


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return build_fixture_bundle(tmp_path_factory.mktemp("fx"))


def make_engine(bundle, tmp_path):
    s = Settings(runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "i.sqlite")
    return Engine(s, anomaly=AnomalyService(bundle))


def run_for(eng, seconds):
    for _ in range(round(seconds / 0.1)):
        eng.tick(0.1)


def test_missing_model_is_reported(tmp_path):
    svc = AnomalyService.from_settings(Settings(model_path=tmp_path / "none.joblib"))
    assert svc.state["status"] == "model_not_loaded"


def test_live_fault_changes_anomaly_state(bundle, tmp_path):
    eng = make_engine(bundle, tmp_path)
    run_id = eng.start_live(seed=7, autopilot=True)
    run_for(eng, 15)
    st = eng.snapshot()["anomaly_state"]
    assert st["status"] == "active" and not st["alarm"]
    eng.inject(
        FaultSpec(
            fault_id="live",
            type="SENSOR_STUCK",
            target="boom_joint.position",
            start_time=0,
            duration=10,
            severity=1.0,
        )
    )
    run_for(eng, 3)
    st = eng.snapshot()["anomaly_state"]
    assert st["alarm"] and "boom_joint.position" in st["affected_sensors"]
    assert "평소와 다른 패턴" in st["message"]
    assert set(st["detectors"]) == {"threshold", "zscore", "zscore-tail", "isolation-forest"}
    report = eng.stop_run()
    assert report["anomaly_events"]
    assert report["fault_to_detection_s"] is not None and report["fault_to_detection_s"] < 3.0
    assert eng.index.path(run_id)


def test_anomaly_alarm_does_not_touch_safety(bundle, tmp_path):
    eng = make_engine(bundle, tmp_path)
    eng.start_live(seed=7, autopilot=True)
    run_for(eng, 10)
    eng.inject(
        FaultSpec(
            fault_id="x",
            type="SENSOR_NOISE",
            target="arm_joint.effort",
            start_time=0,
            duration=10,
            severity=1.0,
        )
    )
    run_for(eng, 3)
    snap = eng.snapshot()
    assert snap["anomaly_state"]["alarm"]
    assert not snap["safety_state"]["estop"]  # an AI alarm never triggers safety actions
    assert snap["safety_state"]["level"] in ("INFO", "CAUTION")


def test_compare_endpoint_on_recorded_run(bundle, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    s = Settings(runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "i.sqlite")
    with TestClient(create_app(s)) as c:
        eng = c.app.state.engine
        eng.anomaly = AnomalyService(bundle)
        run_id = eng.start_live(seed=7, autopilot=True)
        run_for(eng, 12)
        eng.inject(
            FaultSpec(
                fault_id="n",
                type="SENSOR_NOISE",
                target="arm_joint.effort",
                start_time=0,
                duration=6,
                severity=1.0,
            )
        )
        run_for(eng, 6)
        eng.stop_run()
        r = c.get(
            "/api/anomalies/compare",
            params={
                "run_id": run_id,
                "normal_from": 2,
                "normal_to": 11,
                "anom_from": 12.5,
                "anom_to": 17.5,
            },
        ).json()
        top = {row["sensor"]: row for row in r["sensors"][:3]}
        # slow drifts (oil temperature) also shift; the noisy sensor must rank in the top 3
        assert "arm_joint.effort" in top and top["arm_joint.effort"]["hf_ratio"] > 2
        events = c.get("/api/anomalies", params={"run_id": run_id}).json()
        assert any(e["kind"] == "anomaly" for e in events)
