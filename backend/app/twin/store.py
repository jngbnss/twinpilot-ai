"""Twin State Store: latest synchronized state of the virtual machine, built from telemetry.

- orders incoming records by simulation time (never by arrival order)
- tracks per-source sequence gaps and reordering
- marks a sensor stale when its age exceeds 3x its observed period
- keeps a ring-buffer history for charts and anomaly features
- measures generation -> backend receive latency
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from twinpilot_sim.schema import JOINTS, TRACKS, TelemetryRecord
from twinpilot_sim.world import TaskConfig

STALE_FACTOR = 3.0
DEFAULT_PERIOD = 0.2
JOINT_METRICS = ("position", "velocity", "effort", "command", "command_error")
OBJECT_STATE = {0: "resting", 1: "attached", 2: "delivered"}


@dataclass
class SensorState:
    value: float
    unit: str
    stamp: float
    quality: str
    period: float = DEFAULT_PERIOD


@dataclass
class SyncStats:
    received: int = 0
    missing: int = 0
    out_of_order: int = 0
    latency_ms_sum: float = 0.0
    latency_ms_max: float = 0.0
    latency_samples: int = 0

    def as_dict(self) -> dict[str, float]:
        total = max(1, self.received + self.missing)
        return {
            "received": self.received,
            "missing": self.missing,
            "out_of_order": self.out_of_order,
            "missing_ratio": round(self.missing / total, 6),
            "out_of_order_ratio": round(self.out_of_order / max(1, self.received), 6),
            "latency_ms_mean": round(self.latency_ms_sum / max(1, self.latency_samples), 3),
            "latency_ms_max": round(self.latency_ms_max, 3),
        }


@dataclass
class TwinStateStore:
    run_id: str
    mode: str
    task: TaskConfig
    history_seconds: float = 120.0
    sensors: dict[str, SensorState] = field(default_factory=dict)
    sync: SyncStats = field(default_factory=SyncStats)
    simulation_time: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    safety_state: dict[str, Any] = field(default_factory=dict)
    anomaly_state: dict[str, Any] = field(default_factory=dict)
    _last_seq: dict[str, int] = field(default_factory=dict)
    _history: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))

    def reset_history(self) -> None:
        self.sensors.clear()
        self._history.clear()
        self._last_seq.clear()
        self.events.clear()
        self.simulation_time = 0.0

    # --------------------------------------------------------------- ingest
    def ingest(
        self, records: list[TelemetryRecord], receive_ns: int | None = None, measure: bool = True
    ) -> None:
        for r in sorted(records, key=lambda r: (r.simulation_time, r.sequence)):
            self._ingest_one(r, receive_ns, measure)

    def _ingest_one(self, r: TelemetryRecord, receive_ns: int | None, measure: bool) -> None:
        self.sync.received += 1
        last = self._last_seq.get(r.source)
        if last is not None:
            if r.sequence <= last:
                self.sync.out_of_order += 1
            elif r.sequence > last + 1 and r.sequence - last - 1 < 10_000:
                self.sync.missing += r.sequence - last - 1
        if last is None or r.sequence > last:
            self._last_seq[r.source] = r.sequence
        if measure and receive_ns is not None:
            lat = (receive_ns - r.timestamp_ns) / 1e6
            if 0 <= lat < 60_000:
                self.sync.latency_ms_sum += lat
                self.sync.latency_ms_max = max(self.sync.latency_ms_max, lat)
                self.sync.latency_samples += 1

        key = r.key
        prev = self.sensors.get(key)
        if prev is not None and r.simulation_time < prev.stamp:
            return  # older than what we already show: keep counters, don't regress state
        period = prev.period if prev else DEFAULT_PERIOD
        if prev is not None and r.simulation_time > prev.stamp:
            period = 0.8 * period + 0.2 * (r.simulation_time - prev.stamp)
        self.sensors[key] = SensorState(
            r.value, r.unit.value, r.simulation_time, r.quality.value, period
        )
        h = self._history[key]
        h.append((r.simulation_time, r.value))
        while h and h[0][0] < r.simulation_time - self.history_seconds:
            h.popleft()
        self.simulation_time = max(self.simulation_time, r.simulation_time)

    def add_events(self, events: list[dict[str, Any]]) -> None:
        self.events.extend(events)

    # --------------------------------------------------------------- queries
    def value(self, key: str, default: float = 0.0) -> float:
        s = self.sensors.get(key)
        return s.value if s else default

    def is_stale(self, key: str) -> bool:
        s = self.sensors.get(key)
        if s is None:
            return True
        return self.simulation_time - s.stamp > STALE_FACTOR * max(s.period, 0.02) + 1e-6

    def history(
        self, keys: list[str], t_from: float | None = None, t_to: float | None = None
    ) -> dict[str, list[tuple[float, float]]]:
        out = {}
        for k in keys:
            pts = self._history.get(k, ())
            out[k] = [
                (t, v)
                for t, v in pts
                if (t_from is None or t >= t_from) and (t_to is None or t <= t_to)
            ]
        return out

    def keys(self) -> list[str]:
        return sorted(self.sensors)

    # --------------------------------------------------------------- snapshot
    def snapshot(self) -> dict[str, Any]:
        v = self.value
        joints = {j: {m: v(f"{j}.{m}") for m in JOINT_METRICS} for j in JOINTS}
        tracks = {t: {"velocity": v(f"{t}.velocity"), "command": v(f"{t}.command")} for t in TRACKS}
        objects = []
        for o in self.task.objects:
            src = f"obj_{o.object_id}"
            if f"{src}.pos_x" not in self.sensors:
                objects.append(
                    {
                        "object_id": o.object_id,
                        "kind": o.kind,
                        "x": o.x,
                        "y": o.y,
                        "z": o.size_m / 2,
                        "state": "resting",
                        "size_m": o.size_m,
                    }
                )
                continue
            objects.append(
                {
                    "object_id": o.object_id,
                    "kind": o.kind,
                    "x": v(f"{src}.pos_x"),
                    "y": v(f"{src}.pos_y"),
                    "z": v(f"{src}.pos_z"),
                    "state": OBJECT_STATE.get(int(v(f"{src}.state")), "resting"),
                    "size_m": o.size_m,
                }
            )
        sensor_states = {
            k: {
                "value": s.value,
                "unit": s.unit,
                "stamp": round(s.stamp, 3),
                "stale": self.is_stale(k),
                "quality": s.quality,
            }
            for k, s in self.sensors.items()
            if not k.startswith("obj_")
        }
        delivered = int(v("task.delivered"))
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "simulation_time": round(self.simulation_time, 3),
            "vehicle_pose": {
                "x": v("base.pose_x"),
                "y": v("base.pose_y"),
                "yaw": v("imu.yaw"),
                "roll": v("imu.roll"),
                "pitch": v("imu.pitch"),
            },
            "joint_states": joints,
            "tracks": tracks,
            "sensor_states": sensor_states,
            "task_state": {
                "delivered": delivered,
                "target_count": self.task.target_count,
                "progress": round(delivered / max(1, self.task.target_count), 3),
                "time_limit_s": self.task.time_limit_s,
                "objects": objects,
                "payload": v("contact.bucket_payload") > 0.5,
            },
            "safety_state": self.safety_state,
            "anomaly_state": self.anomaly_state,
            "sync": self.sync.as_dict(),
            "stale_sensors": [k for k in sensor_states if sensor_states[k]["stale"]],
        }
