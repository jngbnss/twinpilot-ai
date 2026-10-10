"""Control Safety Layer — deterministic rules between operator commands and the simulator.

This module must never depend on AI / anomaly detection output (decision D-007).
It can only reduce or block commands; it never creates motion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum

from twinpilot_sim.adapter import ActuatorCommand, GroundTruth
from twinpilot_sim.control.mapper import ControlMapper
from twinpilot_sim.kinematics import front_moment, front_pose, to_world
from twinpilot_sim.params import ExcavatorParams
from twinpilot_sim.schema import AXES, AXIS_TO_JOINT, ControlCommand
from twinpilot_sim.world import TaskConfig

WORK_AXES = ("boom", "arm", "bucket")
PREDICT_S = 0.5  # look-ahead horizon for distance rules
PROBE = 0.05  # rad, finite-difference step


class RiskLevel(IntEnum):
    INFO = 0
    CAUTION = 1
    WARNING = 2
    EMERGENCY = 3


@dataclass(frozen=True)
class SafetyDecision:
    command: ActuatorCommand
    level: RiskLevel
    rules: tuple[str, ...]
    estop: bool
    worker_distance: float


@dataclass
class SafetyLayer:
    params: ExcavatorParams
    task: TaskConfig
    mapper: ControlMapper = field(default_factory=ControlMapper)
    estop_latched: bool = False
    last_command: ControlCommand | None = None
    last_command_time: float | None = None
    _prev_out: dict[str, float] = field(default_factory=lambda: dict.fromkeys(AXES, 0.0))

    # ---------------------------------------------------------------- inputs
    def receive(self, cmd: ControlCommand, now: float) -> bool:
        """Register an operator command received at simulation time ``now``."""
        if not self.mapper.accept(cmd):
            return False
        if cmd.estop:
            self.estop_latched = True
        self.last_command = cmd
        self.last_command_time = now
        return True

    def trigger_estop(self) -> None:
        self.estop_latched = True

    def reset_estop(self) -> None:
        """Explicit operator action only. Clears the latch and the stale command."""
        self.estop_latched = False
        self.last_command = None
        self.last_command_time = None

    # ------------------------------------------------------------- decision
    def evaluate(self, now: float, gt: GroundTruth, dt: float) -> SafetyDecision:
        s = self.params.safety
        rules: list[str] = []
        level = RiskLevel.INFO

        def flag(rule: str, lvl: RiskLevel) -> None:
            nonlocal level
            rules.append(rule)
            level = max(level, lvl)

        if self.estop_latched:
            flag("estop", RiskLevel.EMERGENCY)
            return self._out(dict.fromkeys(AXES, 0.0), level, rules, gt)

        if self.last_command is None:
            want = dict.fromkeys(AXES, 0.0)
        elif now - (self.last_command_time or 0.0) > s.command_timeout_s:
            flag("command_timeout", RiskLevel.WARNING)
            want = dict.fromkeys(AXES, 0.0)
        else:
            want = self.last_command.axes()

        requested = dict(want)  # distance rules predict with the operator's full request

        # rate limit: only on increasing magnitude, stopping is always immediate
        max_step = s.max_axis_rate_per_s * dt
        for a in AXES:
            prev, w = self._prev_out[a], want[a]
            if abs(w) > abs(prev) or w * prev < 0:
                limited = prev + max(-max_step, min(max_step, w - prev))
                if w * prev < 0:  # direction reversal: go through zero first
                    limited = 0.0 if abs(prev) <= max_step else prev - math.copysign(max_step, prev)
                if not math.isclose(limited, w):
                    if "rate_limited" not in rules:
                        flag("rate_limited", RiskLevel.INFO)
                    want[a] = limited

        # joint position limits
        for axis, j in AXIS_TO_JOINT.items():
            jp = self.params.joints[j]
            if jp.type != "revolute":
                continue
            q = gt.joints[j].position
            if (want[axis] > 0 and q >= jp.upper_rad - s.limit_margin_rad) or (
                want[axis] < 0 and q <= jp.lower_rad + s.limit_margin_rad
            ):
                want[axis] = 0.0
                if "joint_limit" not in rules:
                    flag("joint_limit", RiskLevel.INFO)

        # tip-over: only allow work motions that reduce the front moment
        tilt = max(abs(gt.roll) / s.max_roll_rad, abs(gt.pitch) / s.max_pitch_rad)
        if tilt >= 1.0:
            flag("tipover_risk", RiskLevel.WARNING)
            want["swing"] = 0.0
            for axis in WORK_AXES:
                if want[axis] * self._moment_gradient(gt, axis) > 0:
                    want[axis] = 0.0
        elif tilt >= 0.8:
            flag("tipover_caution", RiskLevel.CAUTION)

        # worker minimum distance: block motions that bring machine or bucket closer
        d_now = gt.worker_distance
        if d_now < s.worker_min_distance_m * 1.5:
            flag(
                "worker_proximity",
                RiskLevel.WARNING if d_now < s.worker_min_distance_m else RiskLevel.CAUTION,
            )
        if d_now < s.worker_min_distance_m:
            for axis in ("left_track", "right_track", "swing", *WORK_AXES):
                if want[axis] != 0.0 and self._worker_distance_after(gt, requested, axis) < d_now:
                    want[axis] = 0.0
                    if "worker_block" not in rules:
                        flag("worker_block", RiskLevel.WARNING)

        # forbidden zones: block driving into them (or deeper into them)
        if want["left_track"] or want["right_track"]:
            bx, by = self._predict_base(gt, requested)
            for z in self.task.forbidden_zones:
                d_after = z.distance(bx, by) - gt.footprint_radius
                d_cur = z.distance(gt.base_x, gt.base_y) - gt.footprint_radius
                if d_after <= 0 and d_after < d_cur:
                    want["left_track"] = want["right_track"] = 0.0
                    flag("forbidden_zone", RiskLevel.WARNING)
                    break

        return self._out(want, level, rules, gt)

    def _out(
        self, axes: dict[str, float], level: RiskLevel, rules: list[str], gt: GroundTruth
    ) -> SafetyDecision:
        self._prev_out = dict(axes)
        return SafetyDecision(
            ActuatorCommand.from_axes(axes),
            level,
            tuple(rules),
            self.estop_latched,
            gt.worker_distance,
        )

    # -------------------------------------------------------------- helpers
    def _q(self, gt: GroundTruth) -> dict[str, float]:
        return {j: t.position for j, t in gt.joints.items()}

    def _moment_gradient(self, gt: GroundTruth, axis: str) -> float:
        q = self._q(gt)
        j = AXIS_TO_JOINT[axis]

        def m(qq: dict[str, float]) -> float:
            return front_moment(
                self.params, qq["boom_joint"], qq["arm_joint"], qq["bucket_joint"], gt.payload_mass
            )

        q2 = dict(q)
        q2[j] += PROBE
        return m(q2) - m(q)

    def _predict_base(self, gt: GroundTruth, want: dict[str, float]) -> tuple[float, float]:
        b = self.params.body
        v = (want["left_track"] + want["right_track"]) / 2 * b.max_track_speed_mps
        return (
            gt.base_x + v * math.cos(gt.yaw) * PREDICT_S,
            gt.base_y + v * math.sin(gt.yaw) * PREDICT_S,
        )

    def _worker_distance_after(self, gt: GroundTruth, want: dict[str, float], axis: str) -> float:
        w = self.task.worker
        q = self._q(gt)
        bx, by = gt.base_x, gt.base_y
        if axis in ("left_track", "right_track"):
            single = dict.fromkeys(AXES, 0.0)
            single[axis] = want[axis]
            bx, by = self._predict_base(gt, single)
        else:
            j = AXIS_TO_JOINT[axis]
            q[j] += want[axis] * self.params.joints[j].max_velocity_rps * PREDICT_S
        fp = front_pose(self.params, q["boom_joint"], q["arm_joint"], q["bucket_joint"])
        cx, cy, _ = to_world(fp.bucket_center, bx, by, gt.yaw, q["swing_joint"])
        d_body = math.hypot(w.x - bx, w.y - by) - gt.footprint_radius
        return min(d_body, math.hypot(w.x - cx, w.y - cy))
