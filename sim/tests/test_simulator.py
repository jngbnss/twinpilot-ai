import math
from collections import Counter

from twinpilot_sim.adapter import ActuatorCommand
from twinpilot_sim.kinematics import arm_ik, bucket_center_ik, front_pose, wrap_angle
from twinpilot_sim.params import default_params
from twinpilot_sim.schema import JOINTS
from twinpilot_sim.simulator import PythonSimulator

P = default_params()


def run(cmd: ActuatorCommand, seconds: float, sim: PythonSimulator | None = None):
    sim = sim or PythonSimulator(seed=1)
    recs = []
    for _ in range(round(seconds / sim.dt)):
        recs.extend(sim.step(cmd))
    return sim, recs


def test_ten_seconds_emits_all_sensors_at_expected_rates():
    sim, recs = run(ActuatorCommand(boom=0.3, swing=0.2, left_track=0.5, right_track=0.5), 10)
    keys = Counter(r.key for r in recs)
    for j in JOINTS:
        for m in ("position", "velocity", "effort", "command", "command_error"):
            assert keys[f"{j}.{m}"] == 250  # 25 Hz
    for k in ("imu.roll", "imu.pitch", "imu.linear_accel_z", "left_track.velocity", "base.pose_x"):
        assert keys[k] == 250
    for k in (
        "hydraulic.oil_temperature",
        "engine.load",
        "chassis.vibration_rms",
        "energy.consumed",
    ):
        assert keys[k] == 50  # 5 Hz
    assert all(r.quality == "simulated" and r.unit for r in recs)
    # sequence strictly increasing per source
    last: dict[str, int] = {}
    for r in recs:
        assert r.sequence == last.get(r.source, -1) + 1
        last[r.source] = r.sequence


def test_joints_stay_within_limits_under_saturated_command():
    sim, _ = run(ActuatorCommand(boom=1, arm=1, bucket=1), 20)
    gt = sim.ground_truth()
    for j in ("boom_joint", "arm_joint", "bucket_joint"):
        jp = P.joints[j]
        assert jp.lower_rad <= gt.joints[j].position <= jp.upper_rad
    assert math.isclose(gt.joints["boom_joint"].position, P.joints["boom_joint"].upper_rad)
    sim, _ = run(ActuatorCommand(boom=-1, arm=-1, bucket=-1), 20, sim)
    gt = sim.ground_truth()
    assert math.isclose(gt.joints["arm_joint"].position, P.joints["arm_joint"].lower_rad)


def test_joint_velocity_follows_command_with_lag():
    sim, _ = run(ActuatorCommand(boom=0.5), 0.1)
    v_early = sim.ground_truth().joints["boom_joint"].velocity
    sim, _ = run(ActuatorCommand(boom=0.5), 2.0, sim)
    v_late = sim.ground_truth().joints["boom_joint"].velocity
    target = 0.5 * P.joints["boom_joint"].max_velocity_rps
    assert 0 < v_early < 0.5 * target
    assert math.isclose(v_late, target, rel_tol=0.02)


def test_differential_drive():
    sim, _ = run(ActuatorCommand(left_track=1, right_track=1), 5)
    gt = sim.ground_truth()
    assert gt.base_y > -6 + 5  # moved north (facing +y)
    assert abs(gt.base_x) < 0.05
    sim2, _ = run(ActuatorCommand(left_track=-0.5, right_track=0.5), 3)
    assert wrap_angle(sim2.ground_truth().yaw - math.pi / 2) > 0.5  # turned left (ccw)


def test_effort_grows_with_reach():
    near = PythonSimulator(seed=0)
    far = PythonSimulator(seed=0)
    far.q["boom_joint"], far.q["arm_joint"] = 0.2, -0.6
    near.step(ActuatorCommand())
    far.step(ActuatorCommand())
    assert (
        far.ground_truth().joints["boom_joint"].effort
        > near.ground_truth().joints["boom_joint"].effort
    )


def test_temperature_rises_under_load_and_energy_accumulates():
    sim, recs = run(ActuatorCommand(left_track=1, right_track=1, swing=1, boom=0.5), 30)
    temps = [r.value for r in recs if r.key == "hydraulic.oil_temperature"]
    energy = [r.value for r in recs if r.key == "energy.consumed"]
    assert temps[-1] > temps[0]
    assert energy == sorted(energy) and energy[-1] > 0


def test_ik_roundtrip():
    sol = arm_ik(P, 6.5, 1.5)
    assert sol is not None
    fp = front_pose(P, sol[0], sol[1], 0.0)
    assert math.isclose(fp.arm_tip.r, 6.5, abs_tol=1e-6)
    assert math.isclose(fp.arm_tip.z, 1.5, abs_tol=1e-6)
    sol = bucket_center_ik(P, 6.3, 0.6, 0.9)
    fp = front_pose(P, sol[0], sol[1], 0.9)
    assert math.isclose(fp.bucket_center.r, 6.3, abs_tol=1e-3)
    assert math.isclose(fp.bucket_center.z, 0.6, abs_tol=1e-3)
    assert arm_ik(P, 30, 0) is None


def test_grasp_attach_and_deliver():
    """Teleport the front so the bucket center sits on an object, close, carry to B, open."""
    sim = PythonSimulator(seed=0)
    sim.x, sim.y = 0.0, 0.0
    obj = sim.world.objects[0]
    yaw = sim.yaw
    swing = math.atan2(obj.y, obj.x) - yaw
    r = math.hypot(obj.x, obj.y)
    sim.q["swing_joint"] = swing
    sim.q["bucket_joint"] = 0.85
    boom, arm = bucket_center_ik(P, r, obj.z, 0.9)
    sim.q["boom_joint"], sim.q["arm_joint"] = boom, arm
    run(ActuatorCommand(bucket=0.5), 0.3, sim)
    assert sim.ground_truth().payload_id == obj.object_id
    # move to zone B: swing to B direction
    zb = sim.task.zone_b
    sim.q["swing_joint"] = math.atan2(zb.y, zb.x) - yaw
    _, _ = run(ActuatorCommand(bucket=-1), 1.0, sim)
    gt = sim.ground_truth()
    assert gt.payload_id is None
    assert sim.world.counters.delivered == 1
    assert (
        any(e.type == "delivered" for e in gt.events) or sim.world.objects[0].state == "delivered"
    )
