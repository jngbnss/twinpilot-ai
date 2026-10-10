"""Deterministic Python excavator simulator (SimulatorAdapter #1, Level 1 Simulation).

Fixed time step, seeded RNG. Physics is an approximation documented in
docs/simulation-assumptions.md; it is not a validated machine model.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

import numpy as np

from twinpilot_sim.adapter import ActuatorCommand, FaultModel, GroundTruth, JointTruth, NoFaults
from twinpilot_sim.kinematics import front_pose, to_world, wrap_angle
from twinpilot_sim.params import ExcavatorParams, default_params
from twinpilot_sim.schema import AXIS_TO_JOINT, JOINTS, TRACKS, Quality, TelemetryRecord
from twinpilot_sim.units import Unit
from twinpilot_sim.world import TaskConfig, World, WorldEvent, load_task

G = 9.81
DT = 0.02
EPOCH_NS = 1_760_140_800_000_000_000  # fixed offline epoch -> deterministic timestamps
ROLLING_RESISTANCE = 0.1
TILT_GAIN = 0.4
HYDRAULIC_EFFICIENCY = 0.8


def offline_clock(sim_time: float) -> int:
    return EPOCH_NS + round(sim_time * 1e9)


class PythonSimulator:
    name = "twinpilot-python-sim"

    def __init__(
        self,
        run_id: str = "run-local",
        seed: int = 0,
        params: ExcavatorParams | None = None,
        task: TaskConfig | None = None,
        faults: FaultModel | None = None,
        clock: Callable[[float], int] = offline_clock,
        quality: Quality = Quality.SIMULATED,
        payload_scale: float = 1.0,
    ):
        self.run_id = run_id
        self.params = params or default_params()
        self.task = task or load_task()
        self.faults: FaultModel = faults or NoFaults()
        self.clock = clock
        self.quality = quality
        self.payload_scale = payload_scale
        self.dt = DT
        p = self.params
        self.fast_every = max(1, round(1 / (DT * p.sensors.fast_rate_hz)))
        self.slow_every = max(1, round(1 / (DT * p.sensors.slow_rate_hz)))
        self.world = World(
            self.task,
            p.grasp.attach_radius_m,
            p.grasp.close_threshold_rad,
            p.grasp.release_threshold_rad,
        )
        self._nominal_moment = self._front_moment(
            p.joints["boom_joint"].initial_rad,
            p.joints["arm_joint"].initial_rad,
            p.joints["bucket_joint"].initial_rad,
            0.0,
        )
        self.reset(seed)

    # ------------------------------------------------------------------ state
    def reset(self, seed: int) -> None:
        p = self.params
        self.seed = seed
        self.rng = np.random.Generator(np.random.PCG64(seed))
        self.tick = 0
        self._t = 0.0
        sp = self.task.start_pose
        self.x, self.y, self.yaw = sp.x, sp.y, sp.yaw
        self.v_track = [0.0, 0.0]
        self.track_cmd = [0.0, 0.0]
        self.prev_speed = 0.0
        self.accel_fwd = 0.0
        self.omega = 0.0
        self.q = {j: p.joints[j].initial_rad for j in JOINTS}
        self.qd = dict.fromkeys(JOINTS, 0.0)
        self.qd_cmd = dict.fromkeys(JOINTS, 0.0)
        self.effort = dict.fromkeys(JOINTS, 0.0)
        self.temp = p.hydraulic.initial_c
        self.energy_kj = 0.0
        self.hyd_kw = 0.0
        self.engine_kw = p.engine.idle_power_kw
        self.roll = 0.0
        self.pitch = 0.0
        self.seq: dict[str, int] = {}
        self.world.reset()
        self._events: list[WorldEvent] = []

    @property
    def sim_time(self) -> float:
        return self._t

    # ---------------------------------------------------------------- physics
    def _payload_mass(self) -> float:
        return self.world.payload_mass * self.payload_scale

    def _front_moment(self, boom: float, arm: float, bucket: float, payload: float) -> float:
        """Horizontal moment (kg*m) of the front attachment about the swing center."""
        p = self.params
        fp = front_pose(p, boom, arm, bucket)
        lk = p.links
        return (
            lk.boom.mass_kg * (fp.pivot.r + fp.boom_tip.r) / 2
            + lk.arm.mass_kg * (fp.boom_tip.r + fp.arm_tip.r) / 2
            + (lk.bucket.mass_kg + payload) * fp.bucket_center.r
        )

    def _gravity_torques(self, payload: float) -> dict[str, float]:
        p = self.params
        fp = front_pose(p, self.q["boom_joint"], self.q["arm_joint"], self.q["bucket_joint"])
        lk = p.links
        boom_c = (fp.pivot.r + fp.boom_tip.r) / 2
        arm_c = (fp.boom_tip.r + fp.arm_tip.r) / 2
        bc = fp.bucket_center.r
        mb = lk.bucket.mass_kg + payload
        return {
            "boom_joint": G
            * (
                lk.boom.mass_kg * (boom_c - fp.pivot.r)
                + lk.arm.mass_kg * (arm_c - fp.pivot.r)
                + mb * (bc - fp.pivot.r)
            ),
            "arm_joint": G * (lk.arm.mass_kg * (arm_c - fp.boom_tip.r) + mb * (bc - fp.boom_tip.r)),
            "bucket_joint": G * mb * (bc - fp.arm_tip.r),
        }

    def _step_tracks(self, cmd: ActuatorCommand) -> None:
        b = self.params.body
        self.track_cmd = [
            cmd.left_track * b.max_track_speed_mps,
            cmd.right_track * b.max_track_speed_mps,
        ]
        alpha = DT / b.track_time_constant_s
        for i in range(2):
            self.v_track[i] += (self.track_cmd[i] - self.v_track[i]) * alpha
        vl, vr = self.v_track
        v = (vl + vr) / 2
        self.omega = (vr - vl) / b.track_gauge_m
        self.yaw = wrap_angle(self.yaw + self.omega * DT)
        self.x += v * math.cos(self.yaw) * DT
        self.y += v * math.sin(self.yaw) * DT
        self.accel_fwd = (v - self.prev_speed) / DT
        self.prev_speed = v

    def _step_joints(self, cmd: ActuatorCommand) -> None:
        p = self.params
        payload = self._payload_mass()
        grav = self._gravity_torques(payload)
        axes = cmd.axes()
        for axis, j in AXIS_TO_JOINT.items():
            jp = p.joints[j]
            lag, fric = self.faults.joint_factors(j, self._t)
            v_cmd = axes[axis] * jp.max_velocity_rps
            self.qd_cmd[j] = v_cmd
            # friction fault: viscous loss reduces achievable speed
            v_target = v_cmd / (1.0 + 0.6 * (fric - 1.0))
            tau = jp.time_constant_s * lag
            v_prev = self.qd[j]
            v_new = v_prev + (v_target - v_prev) * min(1.0, DT / tau)
            q_new = self.q[j] + v_new * DT
            if jp.type == "revolute":
                lo, hi = jp.lower_rad, jp.upper_rad
                if q_new <= lo or q_new >= hi:
                    q_new = min(max(q_new, lo), hi)
                    v_new = 0.0
            else:
                q_new = wrap_angle(q_new)
            accel = (v_new - v_prev) / DT
            if j == "swing_joint":
                reach_inertia = (
                    self._front_moment(
                        self.q["boom_joint"], self.q["arm_joint"], self.q["bucket_joint"], payload
                    )
                    * 6.0
                )
                inertia = (jp.inertia_kgm2 or 0.0) + reach_inertia
                effort = inertia * accel + jp.viscous_friction * fric * v_new
            else:
                inertia = 2000.0
                effort = grav[j] + jp.viscous_friction * fric * v_new + inertia * accel
            self.q[j], self.qd[j], self.effort[j] = q_new, v_new, effort

    def _step_machine_state(self) -> None:
        p = self.params
        joint_w = sum(abs(self.effort[j] * self.qd[j]) for j in JOINTS)
        track_w = (
            ROLLING_RESISTANCE
            * p.body.mass_kg
            * G
            * (abs(self.v_track[0]) + abs(self.v_track[1]))
            / 2
        )
        self.hyd_kw = (joint_w + track_w) / 1000.0
        self.engine_kw = min(
            p.engine.rated_power_kw, p.engine.idle_power_kw + self.hyd_kw / HYDRAULIC_EFFICIENCY
        )
        self.energy_kj += self.engine_kw * DT
        h = p.hydraulic
        dtemp = (
            h.base_heat_cps
            + h.heat_per_kw * self.hyd_kw
            - h.cooling_coeff * (self.temp - h.ambient_c)
            + self.faults.temperature_rate(self._t)
        )
        self.temp += dtemp * DT

        # tilt: front-attachment moment shift relative to nominal, projected on machine axes
        payload = self._payload_mass()
        m = self._front_moment(
            self.q["boom_joint"], self.q["arm_joint"], self.q["bucket_joint"], payload
        )
        shift = (m - self._nominal_moment) / (p.body.mass_kg + payload)
        sw = self.q["swing_joint"]
        fwd, lat = shift * math.cos(sw), shift * math.sin(sw)
        self.pitch = TILT_GAIN * fwd / p.body.half_track_length_m - 0.01 * self.accel_fwd
        self.roll = TILT_GAIN * lat / p.body.half_track_width_m

    def step(self, command: ActuatorCommand) -> list[TelemetryRecord]:
        bucket_prev = self.q["bucket_joint"]
        self._step_tracks(command)
        self._step_joints(command)
        self._step_machine_state()
        self.tick += 1
        self._t = self.tick * DT

        gt_pose = self._front_world()
        s = self.params.safety
        self._events = self.world.update(
            self._t,
            (self.x, self.y),
            self.params.body.footprint_radius_m,
            gt_pose["bucket_center"],
            bucket_prev,
            self.q["bucket_joint"],
            abs(self.roll) > s.max_roll_rad or abs(self.pitch) > s.max_pitch_rad,
            s.worker_min_distance_m,
        )
        records: list[TelemetryRecord] = []
        if self.tick % self.fast_every == 0:
            self._emit_fast(records)
        if self.tick % self.slow_every == 0:
            self._emit_slow(records)
        return records

    # ---------------------------------------------------------------- sensors
    def _front_world(self) -> dict[str, tuple[float, float, float]]:
        fp = front_pose(
            self.params, self.q["boom_joint"], self.q["arm_joint"], self.q["bucket_joint"]
        )
        sw = self.q["swing_joint"]
        return {
            "bucket_center": to_world(fp.bucket_center, self.x, self.y, self.yaw, sw),
            "bucket_tip": to_world(fp.bucket_tip, self.x, self.y, self.yaw, sw),
        }

    def _noise(self, std: float) -> float:
        return float(self.rng.normal(0.0, std))

    def _emit(
        self, out: list[TelemetryRecord], source: str, metric: str, value: float, unit: Unit
    ) -> None:
        key = f"{source}.{metric}"
        value = self.faults.corrupt(key, value, self._t)
        seq = self.seq.get(source, 0)
        self.seq[source] = seq + 1
        out.append(
            TelemetryRecord(
                run_id=self.run_id,
                timestamp_ns=self.clock(self._t),
                simulation_time=round(self._t, 6),
                source=source,
                metric=metric,
                value=round(value, 6),
                unit=unit,
                quality=self.quality,
                sequence=seq,
            )
        )

    def _emit_fast(self, out: list[TelemetryRecord]) -> None:
        n = self.params.sensors.noise
        for j in JOINTS:
            vel = self.qd[j] + self._noise(n.velocity_rps)
            self._emit(out, j, "position", self.q[j] + self._noise(n.position_rad), Unit.RAD)
            self._emit(out, j, "velocity", vel, Unit.RAD_PER_S)
            self._emit(
                out, j, "effort", self.effort[j] + self._noise(n.effort_nm), Unit.NEWTON_METER
            )
            self._emit(out, j, "command", self.qd_cmd[j], Unit.RAD_PER_S)
            self._emit(out, j, "command_error", self.qd_cmd[j] - vel, Unit.RAD_PER_S)
        for i, tr in enumerate(TRACKS):
            self._emit(
                out, tr, "velocity", self.v_track[i] + self._noise(n.velocity_rps), Unit.METER_PER_S
            )
            self._emit(out, tr, "command", self.track_cmd[i], Unit.METER_PER_S)
        v = (self.v_track[0] + self.v_track[1]) / 2
        self._emit(out, "imu", "roll", self.roll + self._noise(n.imu_angle_rad), Unit.RAD)
        self._emit(out, "imu", "pitch", self.pitch + self._noise(n.imu_angle_rad), Unit.RAD)
        self._emit(out, "imu", "yaw", wrap_angle(self.yaw + self._noise(n.imu_angle_rad)), Unit.RAD)
        self._emit(
            out,
            "imu",
            "angular_velocity_z",
            self.omega + self._noise(n.imu_rate_rps),
            Unit.RAD_PER_S,
        )
        vib = self._vibration()
        self._emit(
            out,
            "imu",
            "linear_accel_x",
            self.accel_fwd + self._noise(n.accel_mps2 + vib * 0.3),
            Unit.METER_PER_S2,
        )
        self._emit(
            out,
            "imu",
            "linear_accel_y",
            v * self.omega + self._noise(n.accel_mps2 + vib * 0.3),
            Unit.METER_PER_S2,
        )
        self._emit(
            out,
            "imu",
            "linear_accel_z",
            G + self._noise(n.accel_mps2 + vib * 0.5),
            Unit.METER_PER_S2,
        )
        self._emit(out, "base", "pose_x", self.x + self._noise(n.pose_m), Unit.METER)
        self._emit(out, "base", "pose_y", self.y + self._noise(n.pose_m), Unit.METER)

    def _vibration(self) -> float:
        vp = self.params.vibration
        speed = abs(self.v_track[0]) + abs(self.v_track[1])
        return vp.base_rms + vp.per_kw * self.hyd_kw + 0.2 * speed

    def _emit_slow(self, out: list[TelemetryRecord]) -> None:
        n = self.params.sensors.noise
        p = self.params
        load = 100.0 * self.engine_kw / p.engine.rated_power_kw
        self._emit(
            out,
            "hydraulic",
            "oil_temperature",
            self.temp + self._noise(n.temperature_c),
            Unit.DEG_C,
        )
        self._emit(out, "engine", "load", load + self._noise(0.3), Unit.PERCENT)
        vib = self._vibration()
        self._emit(
            out,
            "chassis",
            "vibration_rms",
            max(0.0, vib + self._noise(0.02 + 0.05 * vib)),
            Unit.METER_PER_S2,
        )
        self._emit(out, "energy", "consumed", self.energy_kj, Unit.KILOJOULE)
        self._emit(out, "contact", "bucket_payload", 1.0 if self.world.payload else 0.0, Unit.BOOL)
        self._emit(out, "contact", "payload_mass", self._payload_mass(), Unit.KILOGRAM)
        self._emit(out, "task", "delivered", float(self.world.counters.delivered), Unit.COUNT)

    # ----------------------------------------------------------- ground truth
    def ground_truth(self) -> GroundTruth:
        fw = self._front_world()
        bc = fw["bucket_center"]
        fr = self.params.body.footprint_radius_m
        payload = self.world.payload
        return GroundTruth(
            sim_time=self._t,
            base_x=self.x,
            base_y=self.y,
            yaw=self.yaw,
            roll=self.roll,
            pitch=self.pitch,
            linear_speed=(self.v_track[0] + self.v_track[1]) / 2,
            track_speeds=(self.v_track[0], self.v_track[1]),
            joints={
                j: JointTruth(self.q[j], self.qd[j], self.effort[j], self.qd_cmd[j]) for j in JOINTS
            },
            bucket_center=bc,
            bucket_tip=fw["bucket_tip"],
            footprint_radius=fr,
            worker_distance=self.world.worker_distance((self.x, self.y), fr, (bc[0], bc[1])),
            payload_id=payload.object_id if payload else None,
            objects=[replace(o) for o in self.world.objects],
            events=list(self._events),
        )
