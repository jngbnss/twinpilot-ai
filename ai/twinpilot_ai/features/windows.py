"""Sliding-window features shared by offline training and the online backend service.

A window covers ``length_s`` seconds: 50 fast samples (25 Hz) and 10 slow samples (5 Hz).
``features_batch`` is the single code path for both offline (many windows) and online
(one window), so training and serving cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from twinpilot_sim.params import default_params

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
FEATURE_SETS = ("raw", "raw+delta", "raw+delta+rolling", "full", "residual")

_P = default_params()
TIME_CONSTANTS = {j: _P.joints[j].time_constant_s for j in JOINTS}
TRACK_TAU = _P.body.track_time_constant_s


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


FLAT_MIN_RUN = 3


def _flat_run(zero_diff: np.ndarray) -> np.ndarray:
    """Longest run of identical consecutive samples / window, counted only when >= 3.

    Noisy healthy sensors almost never repeat a value; a stuck sensor repeats for the
    whole window.
    """
    run = np.zeros(zero_diff.shape[0])
    best = np.zeros(zero_diff.shape[0])
    for k in range(zero_diff.shape[1]):
        run = np.where(zero_diff[:, k], run + 1, 0)
        best = np.maximum(best, run)
    best[best < FLAT_MIN_RUN] = 0
    return best / zero_diff.shape[1]


def _twin_residual(cmd: np.ndarray, vel: np.ndarray, tau: float, dt: float) -> np.ndarray:
    """Measured velocity minus a nominal first-order actuator model driven by the command.

    This is the digital-twin residual: the nominal model encodes how a healthy joint should
    follow its command (time constant from the parameter file).
    """
    sub = 2  # physics runs at 2x the fast sensor rate
    a = min(1.0, (dt / sub) / tau)
    v_hat = vel[:, 0].copy()
    res = np.zeros_like(vel)
    for k in range(1, vel.shape[1]):
        for _ in range(sub):
            v_hat += (cmd[:, k - 1] - v_hat) * a
        res[:, k] = vel[:, k] - v_hat
    return res[:, 1:]


def _physics_block(
    fast: np.ndarray, slow: np.ndarray, spec: WindowSpec, names: list[str]
) -> list[np.ndarray]:
    idx = {c: i for i, c in enumerate(FAST_CHANNELS)}
    dt = 1.0 / spec.fast_hz
    cols: list[np.ndarray] = []

    def add(v: np.ndarray, name: str) -> None:
        cols.append(v[:, None])
        names.append(name)

    for j in JOINTS:
        e = np.abs(fast[:, :, idx[f"{j}.command_error"]])
        add(e.mean(axis=1), f"{j}.command_error:absmean")
        add(e.max(axis=1), f"{j}.command_error:absmax")
        r = _twin_residual(
            fast[:, :, idx[f"{j}.velocity"]] + fast[:, :, idx[f"{j}.command_error"]],
            fast[:, :, idx[f"{j}.velocity"]],
            TIME_CONSTANTS[j],
            dt,
        )
        add(np.abs(r).mean(axis=1), f"{j}.velocity:twin_absmean")
        add(np.abs(r).max(axis=1), f"{j}.velocity:twin_absmax")
        add(r.mean(axis=1), f"{j}.velocity:twin_mean")
        pos = fast[:, :, idx[f"{j}.position"]]
        vel = fast[:, :, idx[f"{j}.velocity"]]
        dpos = pos[:, -1] - pos[:, 0]
        if j == "swing_joint":
            dpos = (dpos + np.pi) % (2 * np.pi) - np.pi
        add(np.abs(dpos - vel[:, 1:].sum(axis=1) * dt), f"{j}.position:kin_residual")
    for tr in ("left_track", "right_track"):
        r = _twin_residual(
            fast[:, :, idx[f"{tr}.command"]], fast[:, :, idx[f"{tr}.velocity"]], TRACK_TAU, dt
        )
        add(np.abs(r).mean(axis=1), f"{tr}.velocity:twin_absmean")
    # sensor health: flat-lining (stuck) and high-frequency noise level
    for block, chans in ((fast, FAST_CHANNELS), (slow, SLOW_CHANNELS)):
        d1 = np.diff(block, axis=1)
        d2 = np.diff(block, n=2, axis=1)
        for i, c in enumerate(chans):
            if c.endswith(".command"):
                continue
            add(_flat_run(d1[:, :, i] == 0), f"{c}:flat")
            add(np.abs(d2[:, :, i]).mean(axis=1), f"{c}:hf")
    for i, c in enumerate(SLOW_CHANNELS):
        add((slow[:, -1, i] - slow[:, 0, i]) / spec.length_s, f"{c}:slope")
    return cols


def features_batch(
    fast: np.ndarray, slow: np.ndarray, feature_set: str = "full", spec: WindowSpec | None = None
) -> tuple[np.ndarray, list[str]]:
    """fast: (W, n_fast, len(FAST_CHANNELS)), slow: (W, n_slow, len(SLOW_CHANNELS))."""
    spec = spec or DEFAULT_SPEC
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"unknown feature set {feature_set}")
    names: list[str] = []
    cols: list[np.ndarray] = []
    if feature_set != "residual":
        stats = ["mean", "min", "max"]
        if feature_set != "raw":
            stats += ["slope", "mad"]
        if feature_set in ("raw+delta+rolling", "full"):
            stats += ["std"]
        cols += _stats(fast, spec.length_s, names, list(FAST_CHANNELS), tuple(stats))
        cols += _stats(slow, spec.length_s, names, list(SLOW_CHANNELS), tuple(stats))
    if feature_set in ("full", "residual"):
        extra_names: list[str] = []
        extra = _physics_block(fast, slow, spec, extra_names)
        for c, n in zip(extra, extra_names, strict=True):
            if n not in names:
                cols.append(c)
                names.append(n)
    X = np.concatenate([c if c.ndim == 2 else c[:, None] for c in cols], axis=1)
    return X.astype(np.float64), names


def sensor_of(feature_name: str) -> str:
    return feature_name.split(":", 1)[0]
