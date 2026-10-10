from pathlib import Path

import numpy as np
import pytest

from twinpilot_sim.faults import FaultInjector, load_fault_scenario
from twinpilot_sim.schema import FaultSpec
from twinpilot_sim.session import SimSession, make_operator

ROOT = Path(__file__).parents[2]
SCENARIOS = ROOT / "simulation" / "fault-scenarios"


def run_scripted(seed: int, seconds: float, faults=None, stop_when_done=False):
    s = SimSession(f"run-test-{seed}", seed=seed, faults=faults)
    op = make_operator(s)
    recs = []
    while s.sim_time < seconds and not (stop_when_done and op.done):
        s.submit(op.act(s.ground_truth()))
        recs.extend(s.step().records)
    return s, op, recs


def series(recs, key, t0, t1):
    return np.array([r.value for r in recs if r.key == key and t0 <= r.simulation_time < t1])


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_scripted_operator_completes_task(seed):
    s, op, _ = run_scripted(seed, 300, stop_when_done=True)
    m = s.metrics()
    assert op.done
    assert m["delivered"] == 3 and m["completed"] and m["within_time_limit"]
    assert m["worker_collisions"] == 0 and m["forbidden_entries"] == 0
    assert m["tipover_risks"] == 0


def test_all_fault_scenarios_load():
    files = sorted(SCENARIOS.glob("*.yaml"))
    assert len(files) == 5
    for f in files:
        assert load_fault_scenario(f)


@pytest.fixture(scope="module")
def normal_run():
    return run_scripted(1, 45)[2]


def fault_run(name):
    return run_scripted(1, 45, load_fault_scenario(SCENARIOS / name))[2]


def test_joint_lag_increases_command_error(normal_run):
    rec = fault_run("joint_lag_bucket.yaml")
    key = "bucket_joint.command_error"
    n, f = series(normal_run, key, 20, 35), series(rec, key, 20, 35)
    assert np.abs(f).mean() > 1.5 * np.abs(n).mean()
    # outside the fault window the same seed behaves identically
    assert np.allclose(series(normal_run, key, 0, 19.9), series(rec, key, 0, 19.9))


def test_friction_increases_effort_and_error(normal_run):
    rec = fault_run("friction_boom.yaml")
    n = series(normal_run, "boom_joint.command_error", 20, 40)
    f = series(rec, "boom_joint.command_error", 20, 40)
    assert np.abs(f).mean() > 1.3 * np.abs(n).mean()


def test_sensor_noise_increases_variance(normal_run):
    rec = fault_run("sensor_noise_arm_effort.yaml")
    key = "arm_joint.effort"
    n, f = series(normal_run, key, 20, 35), series(rec, key, 20, 35)
    assert np.std(f - n) > 1000


def test_sensor_stuck_freezes_value():
    rec = fault_run("sensor_stuck_swing_position.yaml")
    f = series(rec, "swing_joint.position", 20, 35)
    assert np.ptp(f) == 0.0
    assert np.ptp(series(rec, "swing_joint.position", 35, 45)) > 0


def test_temp_rise(normal_run):
    rec = fault_run("temp_rise_hydraulic.yaml")
    key = "hydraulic.oil_temperature"
    assert series(rec, key, 40, 45).mean() > series(normal_run, key, 40, 45).mean() + 5


def test_injector_runtime_add_and_active():
    inj = FaultInjector([], seed=0)
    inj.add(
        FaultSpec(
            fault_id="f",
            type="JOINT_LAG",
            target="arm_joint",
            start_time=1,
            duration=1,
            severity=0.5,
        )
    )
    assert inj.joint_factors("arm_joint", 1.5) == (3.0, 1.0)
    assert inj.joint_factors("arm_joint", 2.5) == (1.0, 1.0)
    assert len(inj.active(1.5)) == 1
