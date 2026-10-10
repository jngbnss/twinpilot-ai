"""Simulator-agnostic interfaces. PythonSimulator is implementation #1, Gazebo is #2 (Phase 5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from twinpilot_sim.schema import AXES, TelemetryRecord
from twinpilot_sim.world import TaskObject, WorldEvent


@dataclass(frozen=True)
class ActuatorCommand:
    """Normalized [-1, 1] axis demands that already passed the Control Safety Layer."""

    left_track: float = 0.0
    right_track: float = 0.0
    swing: float = 0.0
    boom: float = 0.0
    arm: float = 0.0
    bucket: float = 0.0

    @classmethod
    def from_axes(cls, axes: dict[str, float]) -> ActuatorCommand:
        return cls(**{a: max(-1.0, min(1.0, float(axes.get(a, 0.0)))) for a in AXES})

    def axes(self) -> dict[str, float]:
        return {a: getattr(self, a) for a in AXES}


@dataclass(frozen=True)
class JointTruth:
    position: float
    velocity: float
    effort: float
    command: float


@dataclass(frozen=True)
class GroundTruth:
    """Noise-free simulator state. Used by safety rules and task scoring, never by AI."""

    sim_time: float
    base_x: float
    base_y: float
    yaw: float
    roll: float
    pitch: float
    linear_speed: float
    track_speeds: tuple[float, float]
    joints: dict[str, JointTruth]
    bucket_center: tuple[float, float, float]
    bucket_tip: tuple[float, float, float]
    footprint_radius: float
    worker_distance: float
    payload_id: str | None
    objects: list[TaskObject]
    events: list[WorldEvent] = field(default_factory=list)


class FaultModel(Protocol):
    """Hooks the simulator calls so faults stay outside the physics code."""

    def joint_factors(self, joint: str, t: float) -> tuple[float, float]:
        """(time-constant multiplier, friction multiplier)."""
        ...

    def temperature_rate(self, t: float) -> float:
        """Extra hydraulic heating in degC/s."""
        ...

    def corrupt(self, key: str, value: float, t: float) -> float:
        """Sensor-level corruption of an already noisy measurement."""
        ...


class NoFaults:
    def joint_factors(self, joint: str, t: float) -> tuple[float, float]:
        return 1.0, 1.0

    def temperature_rate(self, t: float) -> float:
        return 0.0

    def corrupt(self, key: str, value: float, t: float) -> float:
        return value


class SimulatorAdapter(Protocol):
    run_id: str

    @property
    def sim_time(self) -> float: ...

    def reset(self, seed: int) -> None: ...

    def step(self, command: ActuatorCommand) -> list[TelemetryRecord]: ...

    def ground_truth(self) -> GroundTruth: ...
