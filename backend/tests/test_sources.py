from app.telemetry.source import LiveSimSource, ReplaySource
from app.twin.store import TwinStateStore
from twinpilot_sim.world import load_task

TASK = load_task()


def drain(source, store, wall_dt=0.1, steps=50):
    for _ in range(steps):
        tick = source.advance(wall_dt)
        store.ingest(tick.records)
        store.add_events(tick.events)


def shape(d):
    if isinstance(d, dict):
        return {k: shape(v) for k, v in d.items() if k not in ("sensor_states",)}
    return type(d).__name__ if not isinstance(d, list) else "list"


def test_live_and_replay_share_state_structure(recorded_run):
    live = LiveSimSource("run-live", seed=1, autopilot=True, clock=lambda t: int(t * 1e9))
    ls = TwinStateStore("run-live", "live", TASK)
    drain(live, ls)
    rp = ReplaySource(recorded_run)
    rs = TwinStateStore(rp.run_id, "replay", TASK)
    rp.play()
    drain(rp, rs)
    a, b = ls.snapshot(), rs.snapshot()
    assert set(a) == set(b)
    assert set(a["sensor_states"]) == set(b["sensor_states"])
    assert shape(a["joint_states"]) == shape(b["joint_states"])
    assert all(s["quality"] == "replayed" for s in b["sensor_states"].values())
    assert all(s["quality"] == "simulated" for s in a["sensor_states"].values())


def test_live_runs_in_real_time():
    live = LiveSimSource("run-live", seed=1)
    live.advance(0.5)
    assert abs(live.sim_time - 0.5) < 0.021


def test_replay_seek_matches_recording(recorded_run):
    rp = ReplaySource(recorded_run)
    store = TwinStateStore(rp.run_id, "replay", TASK)
    rp.seek(25.0)
    store.ingest(rp.advance(0.0).records)
    expected = [
        r for r in rp.records if r.key == "boom_joint.position" and r.simulation_time <= 25.0
    ][-1]
    assert store.value("boom_joint.position") == expected.value
    assert store.simulation_time == expected.simulation_time or store.simulation_time <= 25.0


def test_replay_speed_and_pause(recorded_run):
    rp = ReplaySource(recorded_run)
    rp.advance(0)  # initial seek tick
    rp.set_speed(4)
    rp.play()
    rp.advance(1.0)
    assert abs(rp.sim_time - 4.0) < 1e-9
    rp.pause()
    rp.advance(1.0)
    assert abs(rp.sim_time - 4.0) < 1e-9
    events = rp.events_until(40)
    assert any(e["kind"] == "fault" for e in events)
