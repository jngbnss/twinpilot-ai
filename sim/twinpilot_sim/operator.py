"""Scripted operator: a deterministic state machine that performs the carry task.

It plays the role of a human driver so normal and faulty datasets can be generated without
people. It reads ground truth (what a human would see) and emits ControlCommands, which
still pass through the Control Safety Layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from twinpilot_sim.adapter import GroundTruth
from twinpilot_sim.kinematics import bucket_center_ik, wrap_angle
from twinpilot_sim.params import ExcavatorParams
from twinpilot_sim.schema import ControlCommand
from twinpilot_sim.world import CircleZone, TaskConfig

CARRY_POSE = {"boom_joint": 0.75, "arm_joint": -1.7}
BUCKET_APPROACH = 0.3
BUCKET_CARRY = 1.35
BUCKET_RELEASE = -0.3
JOINT_TOL = 0.03
SWING_TOL = 0.03
RELEASE_HEIGHT = 1.4
GAIN = 3.0


def _clip(v: float) -> float:
    return max(-1.0, min(1.0, v))


@dataclass
class OperatorConfig:
    speed_scale: float = 1.0
    loop: bool = False  # after all objects are moved, carry them back (continuous data)
    jitter_m: float = 0.15
    dwell_s: tuple[float, float] = (0.2, 0.8)


class ScriptedOperator:
    def __init__(
        self,
        params: ExcavatorParams,
        task: TaskConfig,
        seed: int = 0,
        config: OperatorConfig | None = None,
    ):
        self.p = params
        self.task = task
        self.cfg = config or OperatorConfig()
        self.rng = np.random.Generator(np.random.PCG64(seed + 7919))
        self.sequence = 0
        self.state = "drive"
        self.target_id: str | None = None
        self.target_xyz: tuple[float, float, float] | None = None
        self.wait_until = 0.0
        self.source, self.dest = task.zone_a, task.zone_b
        self.slot = 0
        self.retries = 0

    # ----------------------------------------------------------- helpers
    def _joint_axis(self, gt: GroundTruth, joint: str, target: float) -> float:
        jp = self.p.joints[joint]
        err = target - gt.joints[joint].position
        if joint == "swing_joint":
            err = wrap_angle(err)
        return _clip(GAIN * err / max(jp.max_velocity_rps, 1e-6) * jp.max_velocity_rps)

    def _at(self, gt: GroundTruth, targets: dict[str, float]) -> bool:
        for j, q in targets.items():
            err = q - gt.joints[j].position
            if j == "swing_joint":
                err = wrap_angle(err)
            if abs(err) > (SWING_TOL if j == "swing_joint" else JOINT_TOL):
                return False
        return True

    def _swing_to(self, gt: GroundTruth, x: float, y: float) -> float:
        return wrap_angle(math.atan2(y - gt.base_y, x - gt.base_x) - gt.yaw)

    def _reach(self, gt: GroundTruth, x: float, y: float) -> float:
        return math.hypot(x - gt.base_x, y - gt.base_y)

    def _slot_xy(self, zone: CircleZone) -> tuple[float, float]:
        k = self.slot % 3
        ang = math.pi / 2 + k * 2 * math.pi / 3
        j = self.cfg.jitter_m
        return (
            zone.x + 1.0 * math.cos(ang) + float(self.rng.uniform(-j, j)),
            zone.y + 1.0 * math.sin(ang) + float(self.rng.uniform(-j, j)),
        )

    def _next_object(self, gt: GroundTruth):
        cands = [o for o in gt.objects if o.state != "attached" and self.source.contains(o.x, o.y)]
        if not cands:
            return None
        return min(cands, key=lambda o: math.hypot(o.x - gt.base_x, o.y - gt.base_y))

    def _cmd(self, t: float, **axes: float) -> ControlCommand:
        self.sequence += 1
        s = self.cfg.speed_scale
        return ControlCommand(
            timestamp=t,
            device="script",
            sequence=self.sequence,
            **{k: _clip(v * s) for k, v in axes.items()},
        )

    def _dwell(self, t: float) -> None:
        lo, hi = self.cfg.dwell_s
        self.wait_until = t + float(self.rng.uniform(lo, hi))

    # ------------------------------------------------------------- policy
    def act(self, gt: GroundTruth) -> ControlCommand:
        t = gt.sim_time
        if t < self.wait_until:
            return self._cmd(t)
        handler = getattr(self, f"_s_{self.state}")
        return handler(gt, t)

    def _s_drive(self, gt: GroundTruth, t: float) -> ControlCommand:
        wp = self.task.work_position
        dx, dy = wp.x - gt.base_x, wp.y - gt.base_y
        dist = math.hypot(dx, dy)
        if dist < 0.15:
            self.state = "pick_raise"
            self._dwell(t)
            return self._cmd(t)
        heading_err = wrap_angle(math.atan2(dy, dx) - gt.yaw)
        steer = _clip(-2.0 * heading_err)
        drive = _clip(1.2 * dist) if abs(heading_err) < 0.3 else 0.0
        left, right = _clip(drive + steer), _clip(drive - steer)
        return self._cmd(t, left_track=left, right_track=right)

    def _s_pick_raise(self, gt: GroundTruth, t: float) -> ControlCommand:
        obj = self._next_object(gt)
        if obj is None:
            if self.cfg.loop:
                self.source, self.dest = self.dest, self.source
                return self._cmd(t)
            self.state = "done"
            return self._cmd(t)
        self.target_id = obj.object_id
        j = self.cfg.jitter_m * 0.3
        self.target_xyz = (
            obj.x + float(self.rng.uniform(-j, j)),
            obj.y + float(self.rng.uniform(-j, j)),
            obj.z,
        )
        target = dict(CARRY_POSE, swing_joint=self._swing_to(gt, obj.x, obj.y))
        if self._at(gt, target):
            self.state = "pick_lower"
            return self._cmd(t)
        return self._move(gt, t, target, BUCKET_APPROACH)

    def _s_pick_lower(self, gt: GroundTruth, t: float) -> ControlCommand:
        x, y, z = self.target_xyz
        sol = bucket_center_ik(self.p, self._reach(gt, x, y), z, self.p.grasp.close_threshold_rad)
        if sol is None:
            self.state = "pick_raise"
            return self._cmd(t)
        target = {
            "swing_joint": self._swing_to(gt, x, y),
            "boom_joint": sol[0],
            "arm_joint": sol[1],
        }
        if self._at(gt, target):
            self.state = "pick_close"
            return self._cmd(t)
        return self._move(gt, t, target, BUCKET_APPROACH)

    def _s_pick_close(self, gt: GroundTruth, t: float) -> ControlCommand:
        if gt.payload_id is not None:
            self.state = "carry_raise"
            self.retries = 0
            self._dwell(t)
            return self._cmd(t)
        if gt.joints["bucket_joint"].position >= BUCKET_CARRY - JOINT_TOL:
            # missed the grasp: open and retry
            self.retries += 1
            self.state = "pick_reopen"
            return self._cmd(t)
        return self._cmd(t, bucket=1.0)

    def _s_pick_reopen(self, gt: GroundTruth, t: float) -> ControlCommand:
        if gt.joints["bucket_joint"].position <= BUCKET_APPROACH + JOINT_TOL:
            self.state = "pick_raise" if self.retries < 3 else "pick_skip"
            return self._cmd(t)
        return self._cmd(t, bucket=-1.0, boom=0.3)

    def _s_pick_skip(self, gt: GroundTruth, t: float) -> ControlCommand:
        self.state = "done"
        return self._cmd(t)

    def _s_carry_raise(self, gt: GroundTruth, t: float) -> ControlCommand:
        target = dict(CARRY_POSE)
        if self._at(gt, target):
            self.state = "carry_swing"
            self.target_xyz = (*self._slot_xy(self.dest), RELEASE_HEIGHT)
            return self._cmd(t)
        return self._move(gt, t, target, BUCKET_CARRY)

    def _s_carry_swing(self, gt: GroundTruth, t: float) -> ControlCommand:
        x, y, _ = self.target_xyz
        target = dict(CARRY_POSE, swing_joint=self._swing_to(gt, x, y))
        if self._at(gt, target):
            self.state = "carry_lower"
            return self._cmd(t)
        return self._move(gt, t, target, BUCKET_CARRY)

    def _s_carry_lower(self, gt: GroundTruth, t: float) -> ControlCommand:
        x, y, z = self.target_xyz
        sol = bucket_center_ik(self.p, self._reach(gt, x, y), z, BUCKET_CARRY)
        if sol is None:
            self.state = "release"
            return self._cmd(t)
        target = {
            "swing_joint": self._swing_to(gt, x, y),
            "boom_joint": sol[0],
            "arm_joint": sol[1],
        }
        if self._at(gt, target):
            self.state = "release"
            return self._cmd(t)
        return self._move(gt, t, target, BUCKET_CARRY)

    def _s_release(self, gt: GroundTruth, t: float) -> ControlCommand:
        if gt.payload_id is None and gt.joints["bucket_joint"].position <= BUCKET_RELEASE + 0.1:
            self.slot += 1
            self.state = "pick_raise"
            self._dwell(t)
            return self._cmd(t)
        return self._cmd(t, bucket=-1.0)

    def _s_done(self, gt: GroundTruth, t: float) -> ControlCommand:
        return self._cmd(t)

    def _move(
        self, gt: GroundTruth, t: float, target: dict[str, float], bucket: float
    ) -> ControlCommand:
        axes = {"bucket": self._joint_axis(gt, "bucket_joint", bucket)}
        names = {"swing_joint": "swing", "boom_joint": "boom", "arm_joint": "arm"}
        # lift before swinging: hold swing until boom is near carry height when far below
        low = gt.bucket_center[2] < 1.0 and "swing_joint" in target and "boom_joint" not in target
        for j, q in target.items():
            if j == "swing_joint" and low:
                continue
            axes[names[j]] = self._joint_axis(gt, j, q)
        return self._cmd(t, **axes)

    @property
    def done(self) -> bool:
        return self.state == "done"
