import ast
import math
from pathlib import Path

import pytest

from twinpilot_sim.adapter import ActuatorCommand
from twinpilot_sim.control import (
    ControlMapper,
    RiskLevel,
    SafetyLayer,
    gamepad_to_command,
    keyboard_to_command,
)
from twinpilot_sim.params import default_params
from twinpilot_sim.schema import ControlCommand
from twinpilot_sim.simulator import PythonSimulator

P = default_params()


def setup(**pose):
    sim = PythonSimulator(seed=0)
    for k, v in pose.items():
        setattr(sim, k, v)
    sim.step(ActuatorCommand())
    return sim, SafetyLayer(P, sim.task)


def cmd(seq=1, t=0.0, **axes):
    return ControlCommand(timestamp=t, device="api", sequence=seq, **axes)


def settle(layer, sim, seconds=1.0):
    """Run past the rate limiter so the output reaches the requested value."""
    d = None
    for _ in range(round(seconds / sim.dt)):
        d = layer.evaluate(sim.sim_time, sim.ground_truth(), sim.dt)
    return d


# ---------------------------------------------------------------- mapper


def test_keyboard_mapping():
    c = keyboard_to_command({"w", "r", "h"}, 1, 0.0)
    assert (c.left_track, c.right_track, c.boom, c.bucket) == (1, 1, 1, 1)
    c = keyboard_to_command({"d"}, 2, 0.0)
    assert c.left_track == 1 and c.right_track == -1  # turn right in place
    c = keyboard_to_command({"q", "Y"}, 3, 0.0)
    assert c.swing == 1 and c.bucket == -1
    assert keyboard_to_command({" "}, 4, 0.0).estop


def test_gamepad_mapping_with_deadzone():
    c = gamepad_to_command({"left_y": -1.0, "left_x": 0.05, "right_y": -0.5, "rt": 0.8}, {}, 1, 0)
    assert c.left_track == 1 and c.right_track == 1
    assert c.boom == 0.5 and c.bucket == 0.8
    assert gamepad_to_command({}, {"lb": True, "estop": True}, 2, 0).estop


def test_mapper_rejects_out_of_order_sequence():
    m = ControlMapper()
    assert m.accept(cmd(seq=5))
    assert not m.accept(cmd(seq=5))
    assert not m.accept(cmd(seq=3))
    assert m.accept(cmd(seq=6))
    assert m.rejected == 2


# ---------------------------------------------------------------- safety


def test_passthrough_after_rate_limit():
    sim, layer = setup()
    layer.receive(cmd(boom=0.5), sim.sim_time)
    first = layer.evaluate(sim.sim_time, sim.ground_truth(), sim.dt)
    assert "rate_limited" in first.rules
    assert first.command.boom == pytest.approx(P.safety.max_axis_rate_per_s * sim.dt)
    layer.receive(cmd(seq=2, boom=0.5), sim.sim_time)
    d = settle(layer, sim, 0.3)
    assert d.command.boom == 0.5
    assert d.level == RiskLevel.INFO


def test_stop_is_immediate():
    sim, layer = setup()
    layer.receive(cmd(boom=1.0), 0.0)
    settle(layer, sim, 0.4)
    layer.receive(cmd(seq=2, boom=0.0), 0.0)
    assert layer.evaluate(0.0, sim.ground_truth(), sim.dt).command.boom == 0.0


def test_command_timeout_stops_everything():
    sim, layer = setup()
    layer.receive(cmd(left_track=1, right_track=1, boom=1), 0.0)
    d = layer.evaluate(P.safety.command_timeout_s + 0.01, sim.ground_truth(), sim.dt)
    assert "command_timeout" in d.rules
    assert d.command == ActuatorCommand()
    assert d.level == RiskLevel.WARNING


def test_estop_latches_until_reset():
    sim, layer = setup()
    layer.receive(cmd(estop=True), 0.0)
    layer.receive(cmd(seq=2, boom=1.0), 0.0)
    d = layer.evaluate(0.0, sim.ground_truth(), sim.dt)
    assert d.estop and d.level == RiskLevel.EMERGENCY and d.command == ActuatorCommand()
    layer.reset_estop()
    layer.receive(cmd(seq=3, boom=1.0), 0.0)
    assert layer.evaluate(0.0, sim.ground_truth(), sim.dt).command.boom > 0


def test_joint_limit_blocks_outward_motion_only():
    sim, layer = setup()
    sim.q["boom_joint"] = P.joints["boom_joint"].upper_rad
    sim.step(ActuatorCommand())
    layer.receive(cmd(boom=1.0), sim.sim_time)
    d = settle(layer, sim, 0.1)
    assert d.command.boom == 0.0 and "joint_limit" in d.rules
    layer.receive(cmd(seq=2, boom=-1.0), sim.sim_time)
    assert settle(layer, sim, 0.5).command.boom < 0


def test_worker_proximity_blocks_approach():
    sim, layer = setup()
    w = sim.task.worker
    # place the machine 6.5 m from the worker, facing it -> body distance 6.5 - 2.8 = 3.7 m
    sim.x, sim.y = w.x - 6.5, w.y
    sim.yaw = 0.0
    sim.q["swing_joint"] = math.pi  # bucket points away from the worker
    sim.step(ActuatorCommand())
    layer.receive(cmd(left_track=1, right_track=1), sim.sim_time)
    d = settle(layer, sim, 0.5)
    assert d.command.left_track == 0 and d.command.right_track == 0
    assert "worker_block" in d.rules and d.level == RiskLevel.WARNING
    layer.receive(cmd(seq=2, left_track=-1, right_track=-1), sim.sim_time)
    d = settle(layer, sim, 0.5)
    assert d.command.left_track < 0  # moving away is allowed


def test_forbidden_zone_blocks_entry():
    sim, layer = setup()
    zone = sim.task.forbidden_zones[0]
    sim.x, sim.y, sim.yaw = 0.0, zone.ymin - P.body.footprint_radius_m - 0.2, math.pi / 2
    sim.step(ActuatorCommand())
    layer.receive(cmd(left_track=1, right_track=1), sim.sim_time)
    d = settle(layer, sim, 0.5)
    assert d.command.left_track == 0 and "forbidden_zone" in d.rules


def test_tipover_allows_only_moment_reducing_motion():
    sim, layer = setup()
    sim.roll = P.safety.max_roll_rad + 0.01
    sim.q["boom_joint"], sim.q["arm_joint"] = 0.2, -0.8  # far reach
    sim.step(ActuatorCommand())
    sim.roll = P.safety.max_roll_rad + 0.01
    gt = sim.ground_truth()
    layer.receive(cmd(swing=1, arm=1), sim.sim_time)  # arm out increases reach
    for _ in range(20):
        d = layer.evaluate(sim.sim_time, gt, sim.dt)
    assert "tipover_risk" in d.rules
    assert d.command.swing == 0 and d.command.arm == 0
    layer.receive(cmd(seq=2, arm=-1), sim.sim_time)  # fold arm in -> reduces moment
    for _ in range(20):
        d = layer.evaluate(sim.sim_time, gt, sim.dt)
    assert d.command.arm < 0


# ---------------------------------------------------------------- boundary

FORBIDDEN_IMPORTS = ("twinpilot_ai", "app.anomaly", "sklearn", "torch")


def test_safety_layer_does_not_import_ai():
    control_dir = Path(__file__).parents[1] / "twinpilot_sim" / "control"
    for path in control_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert not n.startswith(FORBIDDEN_IMPORTS), f"{path.name} imports {n}"
