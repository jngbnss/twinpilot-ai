import shutil

import pytest

from app.core.config import Settings
from app.engine import Engine, EngineError
from twinpilot_sim.schema import ControlCommand, FaultSpec


@pytest.fixture
def engine(tmp_path):
    s = Settings(runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "index.sqlite")
    return Engine(s)


def run_for(engine, seconds, dt=0.1):
    for _ in range(round(seconds / dt)):
        engine.tick(dt)


def test_live_autopilot_run_report(engine):
    run_id = engine.start_live(seed=1, autopilot=True)
    run_for(engine, 50)
    snap = engine.snapshot()
    assert snap["mode"] == "live" and snap["task_state"]["delivered"] == 3
    report = engine.stop_run()
    assert report["run_id"] == run_id
    raw = report["raw_metrics"]
    assert raw["delivered"] == 3 and raw["completed"]
    assert 0 < report["score"]["total"] <= 100
    assert {"completion", "time", "safety"} <= set(report["score"])
    assert engine.index.path(run_id) is not None
    assert any(r["run_id"] == run_id for r in engine.index.list())


def test_manual_timeout_stops_machine(engine):
    engine.start_live(seed=1)
    engine.submit(ControlCommand(timestamp=0, device="keyboard", sequence=1, boom=1.0))
    run_for(engine, 0.3)
    assert engine.snapshot()["joint_states"]["boom_joint"]["velocity"] > 0.1
    run_for(engine, 1.5)  # no new commands -> timeout
    snap = engine.snapshot()
    assert "command_timeout" in snap["safety_state"]["rules"]
    assert abs(snap["joint_states"]["boom_joint"]["velocity"]) < 0.02


def test_estop_and_reset(engine):
    engine.start_live(seed=1, autopilot=True)
    run_for(engine, 2)
    engine.estop()
    run_for(engine, 1)
    snap = engine.snapshot()
    assert snap["safety_state"]["estop"] and snap["safety_state"]["level"] == "EMERGENCY"
    engine.reset_estop()
    run_for(engine, 0.2)
    assert not engine.snapshot()["safety_state"]["estop"]


def test_runtime_fault_injection_is_labeled(engine):
    run_id = engine.start_live(seed=1, autopilot=True)
    run_for(engine, 5)
    ev = engine.inject(
        FaultSpec(
            fault_id="f-live",
            type="JOINT_LAG",
            target="bucket_joint",
            start_time=0,
            duration=10,
            severity=0.8,
        )
    )
    assert ev["start_time"] >= 4.9  # moved to current sim time
    run_for(engine, 2)
    engine.stop_run()
    report = engine.report_for(run_id)
    assert report["faults"][0]["fault_id"] == "f-live"
    assert report["meta"]["faults"][0]["type"] == "JOINT_LAG"


def test_replay_indexed_cli_run(engine, recorded_run):
    shutil.copytree(recorded_run, engine.settings.runs_dir / recorded_run.name)
    engine.index.sync()
    engine.start_replay(recorded_run.name)
    engine.replay().set_speed(8)
    engine.replay().play()
    run_for(engine, 3)
    snap = engine.snapshot()
    assert snap["mode"] == "replay" and snap["replay"]["cursor"] == pytest.approx(
        23.2, abs=0.1
    )  # 1st tick = seek
    assert any(e.get("kind") == "fault" for e in engine.store.events)
    with pytest.raises(EngineError):
        engine.submit(ControlCommand(timestamp=0, device="api", sequence=1))
