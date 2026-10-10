"""Batch execution of a scripted scenario with recording (CLI, dataset generation)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from twinpilot_sim import __version__
from twinpilot_sim.operator import OperatorConfig
from twinpilot_sim.recorder import RunRecorder, to_parquet
from twinpilot_sim.schema import FaultSpec, RunMetadata
from twinpilot_sim.session import SimSession, make_operator


@dataclass
class RunResult:
    run_dir: Path
    metrics: dict
    records: int


def run_scenario(
    run_id: str,
    seed: int,
    duration: float,
    out_dir: Path | str = "runs",
    faults: list[FaultSpec] | None = None,
    scenario: str = "normal",
    operator: OperatorConfig | None = None,
    payload_scale: float = 1.0,
    until_done: bool = False,
    parquet: bool = False,
) -> RunResult:
    op_cfg = operator or OperatorConfig()
    session = SimSession(run_id, seed=seed, faults=faults, payload_scale=payload_scale)
    op = make_operator(session, op_cfg)
    meta = RunMetadata(
        run_id=run_id,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        sim_version=__version__,
        seed=seed,
        scenario=scenario,
        params_hash=session.params.params_hash(),
        dt=session.dt,
        duration=duration,
        faults=list(faults or []),
        operator={
            "type": "scripted",
            "speed_scale": op_cfg.speed_scale,
            "loop": str(op_cfg.loop),
            "payload_scale": payload_scale,
        },
    )
    rec = RunRecorder(out_dir, meta)
    for f in faults or []:
        rec.log_event({"kind": "fault", "time": f.start_time, **f.model_dump(mode="json")})
    while session.sim_time < duration - 1e-9 and not (until_done and op.done):
        session.submit(op.act(session.ground_truth()))
        step = session.step()
        rec.write(step.records)
        for e in step.events:
            rec.log_event({"kind": "world", "type": e.type, "time": round(e.time, 3), **e.detail})
        for e in step.safety_events:
            rec.log_event(
                {
                    "kind": "safety",
                    "level": e.level,
                    "rules": list(e.rules),
                    "time": round(e.time, 3),
                }
            )
    metrics = session.metrics()
    run_dir = rec.close(metrics, duration=round(session.sim_time, 3))
    if parquet:
        to_parquet(run_dir)
    return RunResult(run_dir, metrics, rec.count)
