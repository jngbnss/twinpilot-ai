"""TelemetrySource: live simulation and recorded replay behind one interface.

Both sources emit ``Tick``s made of TelemetryRecords plus timeline events in the same
format as ``events.jsonl``. The twin state is rebuilt from telemetry only, so live and
replay produce the same state structure (Level 1 and Level 2 share the pipeline).
"""

from __future__ import annotations

import bisect
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from twinpilot_sim.operator import OperatorConfig, ScriptedOperator
from twinpilot_sim.recorder import read_events, read_meta, read_records
from twinpilot_sim.schema import ControlCommand, FaultSpec, Quality, RunMetadata, TelemetryRecord
from twinpilot_sim.session import SimSession, make_operator

Mode = Literal["live", "replay"]


@dataclass
class Tick:
    records: list[TelemetryRecord] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    safety: dict[str, Any] | None = None


class TelemetrySource(Protocol):
    mode: Mode
    run_id: str

    @property
    def sim_time(self) -> float: ...

    @property
    def finished(self) -> bool: ...

    def advance(self, wall_dt: float) -> Tick: ...


def wall_clock(_: float) -> int:
    return time.time_ns()


class LiveSimSource:
    """Runs a SimSession in (scaled) real time. Optional scripted operator (autopilot)."""

    mode: Mode = "live"

    def __init__(
        self,
        run_id: str,
        seed: int,
        autopilot: bool = False,
        max_duration: float = 900.0,
        faults: list[FaultSpec] | None = None,
        clock=wall_clock,
        operator: OperatorConfig | None = None,
    ):
        self.run_id = run_id
        self.seed = seed
        self.session = SimSession(run_id, seed=seed, faults=faults, clock=clock)
        self.operator: ScriptedOperator | None = (
            make_operator(self.session, operator or OperatorConfig(loop=True))
            if autopilot
            else None
        )
        self.max_duration = max_duration
        self._debt = 0.0
        self.last_decision = None

    @property
    def sim_time(self) -> float:
        return self.session.sim_time

    @property
    def finished(self) -> bool:
        return self.sim_time >= self.max_duration

    def submit(self, cmd: ControlCommand) -> bool:
        if self.operator is not None and cmd.device != "script":
            # manual input takes over from the autopilot
            self.operator = None
        return self.session.submit(cmd)

    def inject(self, fault: FaultSpec) -> dict[str, Any]:
        self.session.inject(fault)
        return {"kind": "fault", "time": fault.start_time, **fault.model_dump(mode="json")}

    def advance(self, wall_dt: float) -> Tick:
        tick = Tick()
        self._debt += wall_dt
        dt = self.session.dt
        steps = 0
        while self._debt >= dt and steps < 25 and not self.finished:  # cap catch-up
            self._debt -= dt
            steps += 1
            if self.operator is not None:
                self.session.submit(self.operator.act(self.session.ground_truth()))
            res = self.session.step()
            self.last_decision = res.decision
            tick.records.extend(res.records)
            for e in res.events:
                tick.events.append(
                    {"kind": "world", "type": e.type, "time": round(e.time, 3), **e.detail}
                )
            for e in res.safety_events:
                tick.events.append(
                    {
                        "kind": "safety",
                        "level": e.level,
                        "rules": list(e.rules),
                        "time": round(e.time, 3),
                    }
                )
        if steps == 25:
            self._debt = 0.0
        if self.last_decision is not None:
            d = self.last_decision
            tick.safety = {
                "level": d.level.name,
                "rules": list(d.rules),
                "estop": d.estop,
                "worker_distance": round(d.worker_distance, 3),
                "command": d.command.axes(),
            }
        return tick


class ReplaySource:
    """Replays a recorded run: play / pause / speed / seek. Records become ``replayed``."""

    mode: Mode = "replay"

    def __init__(self, run_dir: Path | str):
        self.run_dir = Path(run_dir)
        self.meta: RunMetadata = read_meta(self.run_dir)
        self.run_id = self.meta.run_id
        recs = sorted(read_records(self.run_dir), key=lambda r: (r.simulation_time, r.source))
        self.records = [r.model_copy(update={"quality": Quality.REPLAYED}) for r in recs]
        self.times = [r.simulation_time for r in self.records]
        self.events = sorted(read_events(self.run_dir), key=lambda e: e.get("time", 0.0))
        self.event_times = [e.get("time", 0.0) for e in self.events]
        self.duration = self.times[-1] if self.times else 0.0
        self.cursor = 0.0
        self.speed = 1.0
        self.playing = False
        self._pending_seek = True

    @property
    def sim_time(self) -> float:
        return self.cursor

    @property
    def finished(self) -> bool:
        return self.cursor >= self.duration

    def play(self) -> None:
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def set_speed(self, speed: float) -> None:
        if not 0.1 <= speed <= 16:
            raise ValueError("speed must be within [0.1, 16]")
        self.speed = speed

    def seek(self, t: float) -> None:
        self.cursor = max(0.0, min(t, self.duration))
        self._pending_seek = True

    def window(self, t0: float, t1: float) -> list[TelemetryRecord]:
        """Records with t0 < simulation_time <= t1."""
        i = bisect.bisect_right(self.times, t0)
        j = bisect.bisect_right(self.times, t1)
        return self.records[i:j]

    def events_until(self, t: float) -> list[dict[str, Any]]:
        return self.events[: bisect.bisect_right(self.event_times, t)]

    def advance(self, wall_dt: float) -> Tick:
        tick = Tick()
        if self._pending_seek:
            # after a seek, deliver the last 2 s so every sensor has a current value
            t = self.cursor
            tick.records = self.window(max(-1.0, t - 2.0), t)
            tick.events = [{"kind": "replay_seek", "time": t}]
            self._pending_seek = False
            return tick
        if not self.playing or self.finished:
            return tick
        t0 = self.cursor
        t1 = min(self.duration, t0 + wall_dt * self.speed)
        self.cursor = t1
        tick.records = self.window(t0, t1)
        i = bisect.bisect_right(self.event_times, t0)
        j = bisect.bisect_right(self.event_times, t1)
        tick.events = self.events[i:j]
        if self.finished:
            self.playing = False
        return tick
