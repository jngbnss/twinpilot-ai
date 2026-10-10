"""REST + WebSocket API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from app.engine import Engine, EngineError
from twinpilot_sim.recorder import read_records
from twinpilot_sim.schema import ControlCommand, FaultSpec, FaultType

router = APIRouter(prefix="/api")


def engine_of(request: Request) -> Engine:
    return request.app.state.engine


def _err(e: EngineError) -> HTTPException:
    msg = str(e)
    return HTTPException(404 if "not found" in msg else 409, msg)


# ------------------------------------------------------------------ models
class StartRun(BaseModel):
    mode: Literal["live", "replay"] = "live"
    seed: int = 1
    autopilot: bool = False
    run_id: str | None = None


class FaultRequest(BaseModel):
    type: FaultType
    target: str
    severity: float = Field(0.6, ge=0, le=1)
    duration: float = Field(15.0, gt=0)
    start_time: float | None = None
    fault_id: str | None = None


class Seek(BaseModel):
    t: float = Field(ge=0)


class Speed(BaseModel):
    speed: float = Field(gt=0)


# ------------------------------------------------------------------ meta
@router.get("/health")
def health() -> dict:
    return {"status": "ok", "level": "L1-simulation"}


@router.get("/config")
def config(request: Request) -> dict[str, Any]:
    """Static scene + machine geometry for the dashboard."""
    eng = engine_of(request)
    p = eng.params
    return {
        "level": "L1-simulation",
        "task": eng.task.model_dump(),
        "machine": {
            "links": p.links.model_dump(),
            "boom_pivot": p.boom_pivot.model_dump(),
            "body": p.body.model_dump(),
            "joints": {k: v.model_dump() for k, v in p.joints.items()},
            "safety": p.safety.model_dump(),
        },
        "fault_types": [t.value for t in FaultType],
    }


# ------------------------------------------------------------------ runs
@router.post("/runs")
def start_run(body: StartRun, request: Request) -> dict:
    eng = engine_of(request)
    try:
        if body.mode == "live":
            run_id = eng.start_live(seed=body.seed, autopilot=body.autopilot)
        else:
            if not body.run_id:
                raise HTTPException(422, "run_id required for replay")
            run_id = eng.start_replay(body.run_id)
    except EngineError as e:
        raise _err(e) from e
    return {"run_id": run_id, "mode": body.mode}


@router.post("/runs/stop")
def stop_run(request: Request) -> dict:
    report = engine_of(request).stop_run()
    return {"stopped": True, "report": report}


@router.get("/runs")
def list_runs(request: Request) -> list[dict]:
    eng = engine_of(request)
    eng.index.sync()
    return eng.index.list()


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    eng = engine_of(request)
    for r in eng.index.list():
        if r["run_id"] == run_id:
            return r
    raise HTTPException(404, f"run not found: {run_id}")


@router.get("/runs/{run_id}/report")
def run_report(run_id: str, request: Request) -> dict:
    try:
        return engine_of(request).report_for(run_id)
    except EngineError as e:
        raise _err(e) from e


@lru_cache(maxsize=4)
def _load_series(path: str) -> dict[str, tuple[list[float], list[float], str]]:
    out: dict[str, tuple[list[float], list[float], str]] = {}
    for r in read_records(Path(path)):
        ts, vs, _ = out.setdefault(r.key, ([], [], r.unit.value))
        ts.append(r.simulation_time)
        vs.append(r.value)
    return out


def recorded_series(eng: Engine, run_id: str) -> dict[str, tuple[list[float], list[float], str]]:
    path = eng.index.path(run_id)
    if path is None:
        raise HTTPException(404, f"run not found: {run_id}")
    return _load_series(str(path))


@router.get("/runs/{run_id}/series")
def run_series(
    run_id: str,
    request: Request,
    keys: str = Query(..., description="comma separated source.metric"),
    t_from: float = 0.0,
    t_to: float = 1e9,
    max_points: int = Query(2000, ge=10, le=20000),
) -> dict:
    data = recorded_series(engine_of(request), run_id)
    out = {}
    for k in keys.split(","):
        if k not in data:
            continue
        ts, vs, unit = data[k]
        idx = [i for i, t in enumerate(ts) if t_from <= t <= t_to]
        step = max(1, len(idx) // max_points)
        idx = idx[::step]
        out[k] = {"unit": unit, "t": [ts[i] for i in idx], "v": [vs[i] for i in idx]}
    return {"run_id": run_id, "series": out}


# ------------------------------------------------------------------ control
@router.post("/control")
def control(cmd: ControlCommand, request: Request) -> dict:
    try:
        return {"accepted": engine_of(request).submit(cmd)}
    except EngineError as e:
        raise _err(e) from e


@router.post("/estop")
def estop(request: Request) -> dict:
    try:
        engine_of(request).estop()
    except EngineError as e:
        raise _err(e) from e
    return {"estop": True}


@router.post("/estop/reset")
def estop_reset(request: Request) -> dict:
    try:
        engine_of(request).reset_estop()
    except EngineError as e:
        raise _err(e) from e
    return {"estop": False}


@router.post("/faults")
def inject_fault(body: FaultRequest, request: Request) -> dict:
    eng = engine_of(request)
    try:
        now = eng._live().sim_time
        fault = FaultSpec(
            fault_id=body.fault_id or f"fault-{uuid.uuid4().hex[:6]}",
            type=body.type,
            target=body.target,
            start_time=body.start_time if body.start_time is not None else now,
            duration=body.duration,
            severity=body.severity,
        )
        return eng.inject(fault)
    except ValidationError as e:
        raise HTTPException(422, json.loads(e.json())) from e
    except EngineError as e:
        raise _err(e) from e


# ------------------------------------------------------------------ replay
@router.post("/replay/play")
def replay_play(request: Request) -> dict:
    try:
        engine_of(request).replay().play()
    except EngineError as e:
        raise _err(e) from e
    return {"playing": True}


@router.post("/replay/pause")
def replay_pause(request: Request) -> dict:
    try:
        engine_of(request).replay().pause()
    except EngineError as e:
        raise _err(e) from e
    return {"playing": False}


@router.post("/replay/seek")
def replay_seek(body: Seek, request: Request) -> dict:
    try:
        engine_of(request).replay().seek(body.t)
    except EngineError as e:
        raise _err(e) from e
    return {"cursor": body.t}


@router.post("/replay/speed")
def replay_speed(body: Speed, request: Request) -> dict:
    try:
        engine_of(request).replay().set_speed(body.speed)
    except (EngineError, ValueError) as e:
        raise HTTPException(409, str(e)) from e
    return {"speed": body.speed}


# ------------------------------------------------------------------ twin
@router.get("/twin/state")
def twin_state(request: Request) -> dict:
    return engine_of(request).snapshot()


@router.get("/twin/keys")
def twin_keys(request: Request) -> list[str]:
    store = engine_of(request).store
    return store.keys() if store else []


@router.get("/twin/history")
def twin_history(
    request: Request, keys: str, t_from: float | None = None, t_to: float | None = None
) -> dict:
    store = engine_of(request).store
    if store is None:
        return {}
    return store.history(keys.split(","), t_from, t_to)


@router.get("/twin/events")
def twin_events(request: Request, since: float = 0.0) -> list[dict]:
    store = engine_of(request).store
    if store is None:
        return []
    return [e for e in store.events if e.get("time", 0.0) >= since]


# ------------------------------------------------------------------ websocket
ws_router = APIRouter()


@ws_router.websocket("/ws/twin")
async def ws_twin(ws: WebSocket) -> None:
    await ws.accept()
    eng: Engine = ws.app.state.engine
    q: asyncio.Queue = asyncio.Queue(maxsize=4)
    eng.subscribers.add(q)
    await ws.send_text(json.dumps({"type": "twin_state", "data": eng.snapshot()}))

    async def sender() -> None:
        while True:
            await ws.send_text(await q.get())

    send_task = asyncio.create_task(sender())
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("type")
            try:
                if kind == "control":
                    ok = eng.submit(ControlCommand.model_validate(msg["data"]))
                    if not ok:
                        await ws.send_text(json.dumps({"type": "control_rejected"}))
                elif kind == "estop":
                    eng.estop()
                elif kind == "ping":
                    await ws.send_text(json.dumps({"type": "pong", "t": msg.get("t")}))
            except (EngineError, ValidationError) as e:
                await ws.send_text(json.dumps({"type": "error", "detail": str(e)[:300]}))
    except WebSocketDisconnect:
        pass
    finally:
        eng.subscribers.discard(q)
        send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await send_task
