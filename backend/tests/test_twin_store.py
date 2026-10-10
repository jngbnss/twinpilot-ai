from app.twin.store import TwinStateStore
from twinpilot_sim.schema import TelemetryRecord
from twinpilot_sim.world import load_task

TASK = load_task()


def rec(t, seq, value=1.0, source="boom_joint", metric="position", ts_ns=None):
    return TelemetryRecord(
        run_id="r",
        timestamp_ns=ts_ns if ts_ns is not None else int(t * 1e9),
        simulation_time=t,
        source=source,
        metric=metric,
        value=value,
        unit="rad",
        quality="simulated",
        sequence=seq,
    )


def store():
    return TwinStateStore("r", "live", TASK)


def test_orders_by_simulation_time_not_arrival():
    s = store()
    s.ingest([rec(0.08, 2, 3.0), rec(0.04, 1, 2.0), rec(0.0, 0, 1.0)])
    assert s.value("boom_joint.position") == 3.0
    assert [v for _, v in s.history(["boom_joint.position"])["boom_joint.position"]] == [1, 2, 3]


def test_late_record_does_not_regress_state():
    s = store()
    s.ingest([rec(0.0, 0, 1.0), rec(0.04, 1, 2.0)])
    s.ingest([rec(0.08, 3, 4.0)])
    s.ingest([rec(0.06, 2, 3.0)])  # arrives late
    assert s.value("boom_joint.position") == 4.0
    assert s.sync.out_of_order == 1


def test_missing_sequence_counted():
    s = store()
    s.ingest([rec(0.0, 0), rec(0.04, 1), rec(0.2, 5)])
    assert s.sync.missing == 3
    assert s.sync.as_dict()["missing_ratio"] > 0


def test_stale_detection():
    s = store()
    s.ingest([rec(i * 0.04, i) for i in range(30)])  # period ~0.04
    s.ingest([rec(1.2 + i * 0.04, i, source="imu", metric="roll") for i in range(5)])
    assert s.is_stale("boom_joint.position")  # last at 1.16, now 1.36
    assert not s.is_stale("imu.roll")
    assert "boom_joint.position" in s.snapshot()["stale_sensors"]


def test_latency_measured():
    s = store()
    s.ingest([rec(0.0, 0, ts_ns=1_000_000_000)], receive_ns=1_005_000_000)
    assert s.sync.as_dict()["latency_ms_mean"] == 5.0


def test_history_window_and_snapshot_shape():
    s = store()
    s.ingest([rec(i * 0.5, i) for i in range(400)])  # 200 s of data
    pts = s.history(["boom_joint.position"])["boom_joint.position"]
    assert pts[0][0] >= 199.5 - 120
    snap = s.snapshot()
    for k in (
        "vehicle_pose",
        "joint_states",
        "task_state",
        "safety_state",
        "anomaly_state",
        "sync",
    ):
        assert k in snap
    assert len(snap["task_state"]["objects"]) == 3
