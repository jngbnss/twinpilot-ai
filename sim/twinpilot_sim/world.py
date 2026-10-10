"""Task world: zones, carried objects, worker mannequin and rule-based grasping (D-002)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

DEFAULT_TASK = Path(__file__).parent / "scenarios" / "excavator_carry.yaml"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pose2(_Model):
    x: float
    y: float
    yaw: float = 0.0


class CircleZone(_Model):
    name: str
    x: float
    y: float
    radius: float

    def contains(self, x: float, y: float) -> bool:
        return math.hypot(x - self.x, y - self.y) <= self.radius


class RectZone(_Model):
    name: str
    xmin: float
    xmax: float
    ymin: float
    ymax: float

    def distance(self, x: float, y: float) -> float:
        dx = max(self.xmin - x, 0.0, x - self.xmax)
        dy = max(self.ymin - y, 0.0, y - self.ymax)
        return math.hypot(dx, dy)

    def intersects_circle(self, x: float, y: float, r: float) -> bool:
        return self.distance(x, y) <= r


class Worker(_Model):
    x: float
    y: float
    radius: float


class ObjectSpec(_Model):
    object_id: str
    kind: str
    mass_kg: float
    size_m: float
    x: float
    y: float


class TaskConfig(_Model):
    name: str
    time_limit_s: float
    target_count: int
    start_pose: Pose2
    work_position: Pose2
    zone_a: CircleZone
    zone_b: CircleZone
    forbidden_zones: list[RectZone]
    worker: Worker
    drop_height_m: float
    objects: list[ObjectSpec]


def load_task(path: Path | str = DEFAULT_TASK) -> TaskConfig:
    with open(path, encoding="utf-8") as f:
        return TaskConfig.model_validate(yaml.safe_load(f))


ObjectState = Literal["resting", "attached", "delivered"]


@dataclass
class TaskObject:
    object_id: str
    kind: str
    mass_kg: float
    size_m: float
    x: float
    y: float
    z: float
    state: ObjectState = "resting"


@dataclass(frozen=True)
class WorldEvent:
    type: str
    time: float
    detail: dict[str, float | str] = field(default_factory=dict)


@dataclass
class TaskCounters:
    delivered: int = 0
    drops: int = 0
    collisions: int = 0
    worker_collisions: int = 0
    worker_proximity: int = 0
    forbidden_entries: int = 0
    tipover_risks: int = 0
    completed_at: float | None = None

    def as_dict(self) -> dict[str, float | int]:
        d: dict[str, float | int] = {k: v for k, v in self.__dict__.items() if k != "completed_at"}
        d["completed_at"] = -1.0 if self.completed_at is None else self.completed_at
        return d


class World:
    def __init__(self, task: TaskConfig, attach_radius: float, close_th: float, release_th: float):
        self.task = task
        self.attach_radius = attach_radius
        self.close_th = close_th
        self.release_th = release_th
        self.reset()

    def reset(self) -> None:
        self.objects = [
            TaskObject(o.object_id, o.kind, o.mass_kg, o.size_m, o.x, o.y, o.size_m / 2)
            for o in self.task.objects
        ]
        self.counters = TaskCounters()
        self._flags: dict[str, bool] = {}

    @property
    def payload(self) -> TaskObject | None:
        return next((o for o in self.objects if o.state == "attached"), None)

    @property
    def payload_mass(self) -> float:
        p = self.payload
        return p.mass_kg if p else 0.0

    def _edge(self, key: str, active: bool) -> bool:
        """True only on the rising edge of a condition (count entries, not ticks)."""
        prev = self._flags.get(key, False)
        self._flags[key] = active
        return active and not prev

    def update(
        self,
        t: float,
        base_xy: tuple[float, float],
        footprint_r: float,
        bucket_center: tuple[float, float, float],
        bucket_prev: float,
        bucket_now: float,
        tilt_exceeded: bool,
        worker_min_distance: float,
    ) -> list[WorldEvent]:
        ev: list[WorldEvent] = []
        bx, by, bz = bucket_center
        payload = self.payload

        # grasp: bucket closes through threshold near a resting object
        if payload is None and bucket_prev < self.close_th <= bucket_now:
            best, best_d = None, self.attach_radius
            for o in self.objects:
                if o.state != "resting":
                    continue
                d = math.dist((o.x, o.y, o.z), bucket_center)
                if d <= best_d:
                    best, best_d = o, d
            if best is not None:
                best.state = "attached"
                ev.append(WorldEvent("attach", t, {"object_id": best.object_id}))
        elif payload is not None and bucket_now < self.release_th:
            payload.x, payload.y, payload.z = bx, by, payload.size_m / 2
            high = bz > self.task.drop_height_m
            if self.task.zone_b.contains(bx, by):
                payload.state = "delivered"
                self.counters.delivered += 1
                ev.append(WorldEvent("delivered", t, {"object_id": payload.object_id}))
                if self.counters.delivered >= self.task.target_count:
                    self.counters.completed_at = t
                    ev.append(WorldEvent("task_complete", t))
            else:
                payload.state = "resting"
            if high or not (self.task.zone_b.contains(bx, by) or self.task.zone_a.contains(bx, by)):
                self.counters.drops += 1
                ev.append(WorldEvent("drop", t, {"object_id": payload.object_id}))
            else:
                ev.append(WorldEvent("release", t, {"object_id": payload.object_id}))

        payload = self.payload
        if payload is not None:
            payload.x, payload.y, payload.z = bx, by, bz

        # body vs resting objects
        hit = any(
            math.hypot(o.x - base_xy[0], o.y - base_xy[1]) < footprint_r + o.size_m / 2
            for o in self.objects
            if o.state != "attached"
        )
        if self._edge("collision", hit):
            self.counters.collisions += 1
            ev.append(WorldEvent("collision", t))

        w = self.task.worker
        d_body = math.hypot(w.x - base_xy[0], w.y - base_xy[1]) - footprint_r
        d_bucket = math.dist((w.x, w.y), (bx, by))
        if self._edge("worker_collision", d_body < w.radius or d_bucket < w.radius + 0.5):
            self.counters.worker_collisions += 1
            ev.append(WorldEvent("worker_collision", t))
        if self._edge("worker_proximity", min(d_body, d_bucket) < worker_min_distance):
            self.counters.worker_proximity += 1
            ev.append(WorldEvent("worker_proximity", t, {"distance": min(d_body, d_bucket)}))

        inside = any(
            z.intersects_circle(base_xy[0], base_xy[1], footprint_r)
            for z in self.task.forbidden_zones
        )
        if self._edge("forbidden", inside):
            self.counters.forbidden_entries += 1
            ev.append(WorldEvent("forbidden_zone_entry", t))

        if self._edge("tipover", tilt_exceeded):
            self.counters.tipover_risks += 1
            ev.append(WorldEvent("tipover_risk", t))

        return ev

    def worker_distance(
        self, base_xy: tuple[float, float], footprint_r: float, bucket_xy: tuple[float, float]
    ) -> float:
        w = self.task.worker
        d_body = math.hypot(w.x - base_xy[0], w.y - base_xy[1]) - footprint_r
        return min(d_body, math.dist((w.x, w.y), bucket_xy))
