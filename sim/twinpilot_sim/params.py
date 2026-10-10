"""Typed loader for excavator physical parameters (single source of truth)."""

from __future__ import annotations

import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_PARAMS = Path(__file__).parent / "params" / "excavator.yaml"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Body(_Model):
    mass_kg: float
    track_gauge_m: float
    max_track_speed_mps: float
    track_time_constant_s: float
    cg_height_m: float
    half_track_length_m: float
    half_track_width_m: float
    footprint_radius_m: float


class Pivot(_Model):
    x_m: float
    z_m: float


class Link(_Model):
    length_m: float
    mass_kg: float


class Links(_Model):
    boom: Link
    arm: Link
    bucket: Link


class Joint(_Model):
    type: Literal["continuous", "revolute"]
    max_velocity_rps: float
    time_constant_s: float
    viscous_friction: float
    initial_rad: float
    lower_rad: float | None = None
    upper_rad: float | None = None
    inertia_kgm2: float | None = None

    @model_validator(mode="after")
    def _limits(self) -> Joint:
        if self.type == "revolute":
            if self.lower_rad is None or self.upper_rad is None:
                raise ValueError("revolute joint requires limits")
            if not self.lower_rad <= self.initial_rad <= self.upper_rad:
                raise ValueError("initial position outside limits")
        return self


class Grasp(_Model):
    attach_radius_m: float
    close_threshold_rad: float
    release_threshold_rad: float


class Hydraulic(_Model):
    ambient_c: float
    initial_c: float
    heat_per_kw: float
    base_heat_cps: float
    cooling_coeff: float


class Engine(_Model):
    rated_power_kw: float
    idle_power_kw: float


class Vibration(_Model):
    base_rms: float
    per_kw: float


class Noise(_Model):
    position_rad: float
    velocity_rps: float
    effort_nm: float
    imu_angle_rad: float
    imu_rate_rps: float
    accel_mps2: float
    temperature_c: float
    pose_m: float


class Sensors(_Model):
    fast_rate_hz: int
    slow_rate_hz: int
    noise: Noise


class Safety(_Model):
    max_roll_rad: float
    max_pitch_rad: float
    worker_min_distance_m: float
    command_timeout_s: float
    max_axis_rate_per_s: float
    limit_margin_rad: float


class ExcavatorParams(_Model):
    body: Body
    boom_pivot: Pivot
    links: Links
    joints: dict[str, Joint]
    grasp: Grasp
    hydraulic: Hydraulic
    engine: Engine
    vibration: Vibration
    sensors: Sensors
    safety: Safety

    def params_hash(self) -> str:
        blob = json.dumps(self.model_dump(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def load_params(path: Path | str = DEFAULT_PARAMS) -> ExcavatorParams:
    with open(path, encoding="utf-8") as f:
        return ExcavatorParams.model_validate(yaml.safe_load(f))


@cache
def default_params() -> ExcavatorParams:
    return load_params()
