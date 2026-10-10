"""Sliding-window features shared by offline training and the online backend service.

A window covers ``length_s`` seconds: 50 fast samples (25 Hz) and 10 slow samples (5 Hz).
``features_batch`` is the single code path for both offline (many windows) and online
(one window), so training and serving cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

JOINTS = ("swing_joint", "boom_joint", "arm_joint", "bucket_joint")
FAST_CHANNELS: tuple[str, ...] = (
    *(f"{j}.{m}" for j in JOINTS for m in ("position", "velocity", "effort", "command_error")),
    "left_track.velocity",
    "left_track.command",
    "right_track.velocity",
    "right_track.command",
    "imu.roll",
    "imu.pitch",
    "imu.angular_velocity_z",
    "imu.linear_accel_x",
    "imu.linear_accel_y",
    "imu.linear_accel_z",
)
SLOW_CHANNELS: tuple[str, ...] = (
    "hydraulic.oil_temperature",
    "engine.load",
    "chassis.vibration_rms",
)
FEATURE_SETS = ("raw", "raw+delta", "raw+delta+rolling", "full")


@dataclass(frozen=True)
class WindowSpec:
    length_s: float = 2.0
    stride_s: float = 0.5
    fast_hz: int = 25
    slow_hz: int = 5

    @property
    def n_fast(self) -> int:
        return round(self.length_s * self.fast_hz)

    @property
    def n_slow(self) -> int:
        return round(self.length_s * self.slow_hz)


DEFAULT_SPEC = WindowSpec()


def _stats(x: np.ndarray, length_s: float, names: list[str], prefix: list[str], which: tuple):
    """x: (W, N, C). Returns list of (W, C) arrays and appends names."""
    out = []
    for stat in which:
        if stat == "mean":
            v = x.mean(axis=1)
        elif stat == "min":
            v = x.min(axis=1)
        elif stat == "max":
            v = x.max(axis=1)
        elif stat == "std":
            v = x.std(axis=1)
        elif stat == "slope":
            v = (x[:, -1, :] - x[:, 0, :]) / length_s
        elif stat == "mad":
            v = np.abs(np.diff(x, axis=1)).mean(axis=1)
        else:
            raise ValueError(stat)
        out.append(v)
        names.extend(f"{p}:{stat}" for p in prefix)
    return out


def features_batch(
    fast: np.ndarray, slow: np.ndarray, feature_set: str = "full", spec: WindowSpec | None = None
) -> tuple[np.ndarray, list[str]]:
    """fast: (W, n_fast, len(FAST_CHANNELS)), slow: (W, n_slow, len(SLOW_CHANNELS))."""
    spec = spec or DEFAULT_SPEC
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"unknown feature set {feature_set}")
    stats = ["mean", "min", "max"]
    if feature_set != "raw":
        stats += ["slope", "mad"]
    if feature_set in ("raw+delta+rolling", "full"):
        stats += ["std"]
    names: list[str] = []
    cols = _stats(fast, spec.length_s, names, list(FAST_CHANNELS), tuple(stats))
    cols += _stats(slow, spec.length_s, names, list(SLOW_CHANNELS), tuple(stats))
    if feature_set == "full":
        idx = {c: i for i, c in enumerate(FAST_CHANNELS)}
        # command tracking error magnitude (joints + tracks)
        for j in JOINTS:
            e = np.abs(fast[:, :, idx[f"{j}.command_error"]])
            cols += [e.mean(axis=1, keepdims=False)[:, None], e.max(axis=1)[:, None]]
            names += [f"{j}.command_error:absmean", f"{j}.command_error:absmax"]
        for tr in ("left_track", "right_track"):
            e = np.abs(fast[:, :, idx[f"{tr}.velocity"]] - fast[:, :, idx[f"{tr}.command"]])
            cols += [e.mean(axis=1)[:, None]]
            names += [f"{tr}.velocity:cmd_absmean"]
        # kinematic consistency: position change vs integrated velocity
        dt = 1.0 / spec.fast_hz
        for j in JOINTS:
            pos = fast[:, :, idx[f"{j}.position"]]
            vel = fast[:, :, idx[f"{j}.velocity"]]
            dpos = pos[:, -1] - pos[:, 0]
            if j == "swing_joint":
                dpos = (dpos + np.pi) % (2 * np.pi) - np.pi
            integ = vel[:, 1:].sum(axis=1) * dt
            cols += [np.abs(dpos - integ)[:, None]]
            names += [f"{j}.position:kin_residual"]
    X = np.concatenate([c if c.ndim == 2 else c[:, None] for c in cols], axis=1)
    return X.astype(np.float64), names


def sensor_of(feature_name: str) -> str:
    return feature_name.split(":", 1)[0]
