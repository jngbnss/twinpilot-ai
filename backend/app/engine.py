"""Engine: owns the active TelemetrySource, Twin State Store, recorder and subscribers.

One active run at a time (live or replay). A background asyncio loop advances the source
at ``tick_hz`` and pushes snapshots to WebSocket subscribers at ``broadcast_hz``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.core.config import Settings
from app.runs.index import RunIndex
from app.tasks.report import build_report
from app.telemetry.source import LiveSimSource, ReplaySource, Tick
from app.twin.store import TwinStateStore
from twinpilot_sim import __version__
from twinpilot_sim.params import default_params
from twinpilot_sim.recorder import RunRecorder, read_events, read_meta
from twinpilot_sim.schema import ControlCommand, FaultSpec, RunMetadata
from twinpilot_sim.world import load_task


class AnomalyHook(Protocol):
    """Read-only consumer of twin state (Phase 3). It cannot reach the safety layer."""

    def reset(self, run_id: str) -> None: ...

    def update(self, store: TwinStateStore) -> list[dict[str, Any]]: ...


class EngineError(RuntimeError):
    pass


class Engine:
    def __init__(self, settings: Settings, anomaly: AnomalyHook | None = None):
        self.settings = settings
        self.task = load_task()
        self.params = default_params()
        self.index = RunIndex(settings.db_path, settings.runs_dir)
        self.index.sync()
        self.anomaly = anomaly
        self.source: LiveSimSource | ReplaySource | None = None
        self.store: TwinStateStore | None = None
        self.recorder: RunRecorder | None = None
        self.subscribers: set[asyncio.Queue] = set()
        self._loop_task: asyncio.Task | None = None
        self._last_wall: float | None = None
        self._last_report: dict[str, Any] | None = None

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._run())

    async def shutdown(self) -> None:
        if self.source is not None and self.source.mode == "live":
            self.stop_run()
        if self._loop_task:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

    async def _run(self) -> None:
        period = 1.0 / self.settings.tick_hz
        every = max(1, round(self.settings.tick_hz / self.settings.broadcast_hz))
        n = 0
        while True:
            self.tick()
            n += 1
            if n % every == 0 and self.subscribers and self.store is not None:
                self._broadcast()
            await asyncio.sleep(period)

    def _broadcast(self) -> None:
        msg = json.dumps({"type": "twin_state", "data": self.snapshot()})
        for q in list(self.subscribers):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(msg)

    # ---------------------------------------------------------------- ticking
    def tick(self, wall_dt: float | None = None) -> Tick | None:
        now = time.perf_counter()
        if wall_dt is None:
            wall_dt = 0.0 if self._last_wall is None else now - self._last_wall
        self._last_wall = now
        if self.source is None or self.store is None:
            return None
        t = self.source.advance(min(wall_dt, 0.5))
        if t.events and any(e.get("kind") == "replay_seek" for e in t.events):
            self.store.reset_history()
            if isinstance(self.source, ReplaySource):
                self.store.add_events(self.source.events_until(self.source.cursor))
        receive_ns = time.time_ns()
        self.store.ingest(t.records, receive_ns, measure=self.source.mode == "live")
        self.store.add_events([e for e in t.events if e.get("kind") != "replay_seek"])
        if t.safety is not None:
            self.store.safety_state = t.safety
        elif self.source.mode == "replay":
            self.store.safety_state = self._replay_safety()
        if self.recorder is not None:
            self.recorder.write(t.records)
            for e in t.events:
                self.recorder.log_event(e)
        if self.anomaly is not None and t.records:
            for e in self.anomaly.update(self.store):
                self.store.add_events([e])
                if self.recorder is not None:
                    self.recorder.log_event(e)
        if self.source.mode == "live" and self.source.finished:
            self.stop_run()
        return t

    def _replay_safety(self) -> dict[str, Any]:
        last = next((e for e in reversed(self.store.events) if e.get("kind") == "safety"), None)
        if last is None or self.store.simulation_time - last["time"] > 3.0:
            return {"level": "INFO", "rules": [], "estop": False}
        return {"level": last["level"], "rules": last["rules"], "estop": "estop" in last["rules"]}

    # ------------------------------------------------------------------ runs
    def start_live(
        self, seed: int = 1, autopilot: bool = False, faults: list[FaultSpec] | None = None
    ) -> str:
        if self.source is not None and self.source.mode == "live":
            self.stop_run()
        run_id = datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S-live")
        src = LiveSimSource(
            run_id, seed, autopilot=autopilot, max_duration=self.settings.max_live_duration_s
        )
        meta = RunMetadata(
            run_id=run_id,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            sim_version=__version__,
            seed=seed,
            scenario="live-autopilot" if autopilot else "live-manual",
            params_hash=self.params.params_hash(),
            dt=src.session.dt,
            duration=0.0,
            operator={"type": "scripted" if autopilot else "human"},
        )
        self.recorder = RunRecorder(self.settings.runs_dir, meta)
        self._activate(
            src, TwinStateStore(run_id, "live", self.task, self.settings.history_seconds)
        )
        for f in faults or []:
            self.inject(f)
        return run_id

    def start_replay(self, run_id: str) -> str:
        path = self.index.path(run_id)
        if path is None:
            self.index.sync()
            path = self.index.path(run_id)
        if path is None:
            raise EngineError(f"run not found: {run_id}")
        if self.source is not None and self.source.mode == "live":
            self.stop_run()
        src = ReplaySource(path)
        self.recorder = None
        self._activate(
            src, TwinStateStore(run_id, "replay", self.task, self.settings.history_seconds)
        )
        return run_id

    def _activate(self, src, store: TwinStateStore) -> None:
        self.source, self.store = src, store
        self._last_wall = None
        self._last_report = None
        if self.anomaly is not None:
            self.anomaly.reset(src.run_id)

    def stop_run(self) -> dict[str, Any] | None:
        if self.source is None:
            return None
        report = None
        if isinstance(self.source, LiveSimSource) and self.recorder is not None:
            metrics = self.source.session.metrics()
            run_dir = self.recorder.close(metrics, duration=round(self.source.sim_time, 3))
            self.index.upsert(run_dir)
            report = self.report_for(self.source.run_id)
            self.recorder = None
        self._last_report = report
        self.source = None
        return report

    # --------------------------------------------------------------- control
    def _live(self) -> LiveSimSource:
        if not isinstance(self.source, LiveSimSource):
            raise EngineError("no live run")
        return self.source

    def submit(self, cmd: ControlCommand) -> bool:
        return self._live().submit(cmd)

    def estop(self) -> None:
        self._live().session.safety.trigger_estop()

    def reset_estop(self) -> None:
        self._live().session.safety.reset_estop()

    def inject(self, fault: FaultSpec) -> dict[str, Any]:
        src = self._live()
        if fault.start_time < src.sim_time:
            fault = fault.model_copy(update={"start_time": round(src.sim_time, 3)})
        ev = src.inject(fault)
        if self.store is not None:
            self.store.add_events([ev])
        if self.recorder is not None:
            self.recorder.meta.faults.append(fault)
            self.recorder.log_event(ev)
        return ev

    def replay(self) -> ReplaySource:
        if not isinstance(self.source, ReplaySource):
            raise EngineError("no replay run")
        return self.source

    # ---------------------------------------------------------------- views
    def snapshot(self) -> dict[str, Any]:
        if self.store is None:
            return {"run_id": None, "mode": "idle"}
        snap = self.store.snapshot()
        if isinstance(self.source, ReplaySource):
            snap["replay"] = {
                "cursor": round(self.source.cursor, 3),
                "duration": self.source.duration,
                "speed": self.source.speed,
                "playing": self.source.playing,
            }
        snap["active"] = self.source is not None
        snap["autopilot"] = (
            isinstance(self.source, LiveSimSource) and self.source.operator is not None
        )
        return snap

    def report_for(self, run_id: str) -> dict[str, Any]:
        path: Path | None = self.index.path(run_id)
        if path is None:
            raise EngineError(f"run not found: {run_id}")
        meta = read_meta(path)
        return build_report(
            run_id,
            meta.result,
            read_events(path),
            self.task.time_limit_s,
            meta.model_dump(mode="json", exclude={"result"}),
        )
