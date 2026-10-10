"""Fault injection. Faults are applied through FaultModel hooks so physics code stays clean.

Each FaultSpec is also the ground-truth label used for anomaly-detection evaluation.
A dedicated RNG stream keeps normal-sensor noise identical between a normal run and a
faulty run with the same seed, which makes normal/abnormal comparison exact.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import numpy as np
import yaml

from twinpilot_sim.schema import FaultSpec, FaultType

FAULT_RNG_OFFSET = 10_007

# noise std at severity 1.0 per metric (SENSOR_NOISE)
NOISE_SCALE = {
    "position": 0.03,
    "velocity": 0.08,
    "effort": 4000.0,
    "command_error": 0.08,
    "roll": 0.02,
    "pitch": 0.02,
    "yaw": 0.02,
    "angular_velocity_z": 0.05,
    "linear_accel_x": 0.6,
    "linear_accel_y": 0.6,
    "linear_accel_z": 0.8,
    "oil_temperature": 2.0,
    "vibration_rms": 0.4,
    "load": 6.0,
    "pose_x": 0.2,
    "pose_y": 0.2,
}


class FaultInjector:
    def __init__(self, faults: Iterable[FaultSpec], seed: int = 0):
        self.faults = list(faults)
        self.rng = np.random.Generator(np.random.PCG64(seed + FAULT_RNG_OFFSET))
        self._stuck: dict[str, float] = {}

    def add(self, fault: FaultSpec) -> None:
        self.faults.append(fault)

    def active(self, t: float) -> list[FaultSpec]:
        return [f for f in self.faults if f.active(t)]

    def joint_factors(self, joint: str, t: float) -> tuple[float, float]:
        lag, fric = 1.0, 1.0
        for f in self.faults:
            if f.target != joint or not f.active(t):
                continue
            if f.type is FaultType.JOINT_LAG:
                lag *= 1.0 + 4.0 * f.severity
            elif f.type is FaultType.FRICTION_INCREASE:
                fric *= 1.0 + 3.0 * f.severity
        return lag, fric

    def temperature_rate(self, t: float) -> float:
        rate = 0.0
        for f in self.faults:
            if not f.active(t):
                continue
            if f.type is FaultType.TEMP_RISE:
                rate += 0.5 * f.severity
            elif f.type is FaultType.FRICTION_INCREASE:
                rate += 0.05 * f.severity
        return rate

    def corrupt(self, key: str, value: float, t: float) -> float:
        for f in self.faults:
            if f.target != key:
                continue
            if f.type is FaultType.SENSOR_STUCK:
                if f.active(t):
                    return self._stuck.setdefault(f.fault_id, value)
            elif f.type is FaultType.SENSOR_NOISE and f.active(t):
                metric = key.split(".", 1)[1]
                scale = NOISE_SCALE.get(metric, 0.05 * abs(value) + 0.01)
                value += float(self.rng.normal(0.0, scale * f.severity))
        return value


def load_fault_scenario(path: Path | str) -> list[FaultSpec]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    items = data.get("faults", data) if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = [items]
    return [FaultSpec.model_validate(i) for i in items]
