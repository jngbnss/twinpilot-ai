"""Common data contracts shared by simulator, backend, AI and replay.

Every sensor value is stored as a ``TelemetryRecord`` with an explicit unit, data quality,
simulation time, wall-clock time and per-source sequence number.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from twinpilot_sim.units import Unit

IDENT = r"^[a-z][a-z0-9_]*$"

JOINTS = ("swing_joint", "boom_joint", "arm_joint", "bucket_joint")
TRACKS = ("left_track", "right_track")
AXES = ("left_track", "right_track", "swing", "boom", "arm", "bucket")
AXIS_TO_JOINT = {
    "swing": "swing_joint",
    "boom": "boom_joint",
    "arm": "arm_joint",
    "bucket": "bucket_joint",
}


class Quality(StrEnum):
    SIMULATED = "simulated"
    REPLAYED = "replayed"
    REAL = "real"


class TelemetryRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    timestamp_ns: int = Field(ge=0, description="wall-clock time when the record was produced")
    simulation_time: float = Field(ge=0, description="simulation time in seconds")
    source: str = Field(pattern=IDENT)
    metric: str = Field(pattern=IDENT)
    value: float
    unit: Unit
    quality: Quality
    sequence: int = Field(ge=0, description="monotonic per source")

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("value must be finite")
        return v

    @property
    def key(self) -> str:
        return f"{self.source}.{self.metric}"


class ControlCommand(BaseModel):
    """Full operator input snapshot. Axes are normalized to [-1, 1].

    A snapshot (instead of one command per message) lets the safety layer apply
    timeouts and rate limits to every axis consistently (decision D-010).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: float = Field(ge=0, description="sender time in seconds")
    device: Literal["keyboard", "gamepad", "script", "api"]
    sequence: int = Field(ge=0)
    left_track: float = Field(0.0, ge=-1, le=1)
    right_track: float = Field(0.0, ge=-1, le=1)
    swing: float = Field(0.0, ge=-1, le=1)
    boom: float = Field(0.0, ge=-1, le=1)
    arm: float = Field(0.0, ge=-1, le=1)
    bucket: float = Field(0.0, ge=-1, le=1)
    estop: bool = False

    def axes(self) -> dict[str, float]:
        return {a: getattr(self, a) for a in AXES}


class FaultType(StrEnum):
    JOINT_LAG = "JOINT_LAG"
    FRICTION_INCREASE = "FRICTION_INCREASE"
    SENSOR_NOISE = "SENSOR_NOISE"
    SENSOR_STUCK = "SENSOR_STUCK"
    TEMP_RISE = "TEMP_RISE"


JOINT_FAULTS = {FaultType.JOINT_LAG, FaultType.FRICTION_INCREASE}
SENSOR_FAULTS = {FaultType.SENSOR_NOISE, FaultType.SENSOR_STUCK}


class FaultSpec(BaseModel):
    """Injected fault. Stored as the ground-truth label for evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fault_id: str = Field(min_length=1)
    type: FaultType
    target: str
    start_time: float = Field(ge=0)
    duration: float = Field(gt=0)
    severity: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _check_target(self) -> FaultSpec:
        if self.type in JOINT_FAULTS and self.target not in JOINTS:
            raise ValueError(f"{self.type} target must be one of {JOINTS}")
        if self.type in SENSOR_FAULTS and "." not in self.target:
            raise ValueError(f"{self.type} target must be 'source.metric'")
        if self.type is FaultType.TEMP_RISE and self.target != "hydraulic":
            raise ValueError("TEMP_RISE target must be 'hydraulic'")
        return self

    @property
    def end_time(self) -> float:
        return self.start_time + self.duration

    def active(self, t: float) -> bool:
        return self.start_time <= t < self.end_time


class RunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str
    level: Literal["L1-simulation", "L2-replay"] = "L1-simulation"
    simulator: str = "twinpilot_sim"
    sim_version: str
    seed: int
    scenario: str
    params_hash: str
    dt: float
    duration: float
    faults: list[FaultSpec] = Field(default_factory=list)
    operator: dict[str, float | str] = Field(default_factory=dict)
    result: dict[str, float | int | bool | str] = Field(default_factory=dict)
