"""Anomaly events and normal-vs-abnormal range comparison."""

from __future__ import annotations

import math

from fastapi import APIRouter, HTTPException, Request

from app.api.routes import engine_of, recorded_series
from twinpilot_sim.recorder import read_events

router = APIRouter(prefix="/api/anomalies")

COMPARE_KEYS = (
    *(
        f"{j}.{m}"
        for j in ("swing_joint", "boom_joint", "arm_joint", "bucket_joint")
        for m in ("position", "velocity", "effort", "command_error")
    ),
    "imu.roll",
    "imu.pitch",
    "hydraulic.oil_temperature",
    "chassis.vibration_rms",
    "engine.load",
)


@router.get("")
def anomalies(request: Request, run_id: str | None = None) -> list[dict]:
    eng = engine_of(request)
    if run_id is None or (eng.store is not None and eng.store.run_id == run_id):
        events = eng.store.events if eng.store else []
    else:
        path = eng.index.path(run_id)
        if path is None:
            raise HTTPException(404, f"run not found: {run_id}")
        events = read_events(path)
    return [e for e in events if e.get("kind") in ("anomaly", "anomaly_cleared", "fault")]


def _range_stats(ts: list[float], vs: list[float], a: float, b: float) -> dict | None:
    sel = [v for t, v in zip(ts, vs, strict=True) if a <= t <= b]
    if len(sel) < 2:
        return None
    n = len(sel)
    mean = sum(sel) / n
    std = (sum((v - mean) ** 2 for v in sel) / (n - 1)) ** 0.5
    d = [b2 - b1 for b1, b2 in zip(sel, sel[1:], strict=False)]
    dm = sum(d) / len(d)
    hf = (sum((x - dm) ** 2 for x in d) / max(1, len(d) - 1)) ** 0.5
    return {"mean": round(mean, 6), "std": round(std, 6), "hf_std": round(hf, 6), "n": n}


@router.get("/compare")
def compare(
    request: Request,
    run_id: str,
    normal_from: float,
    normal_to: float,
    anom_from: float,
    anom_to: float,
    keys: str | None = None,
) -> dict:
    """Per-sensor statistics of a normal range vs an abnormal range of the same run."""
    eng = engine_of(request)
    wanted = keys.split(",") if keys else list(COMPARE_KEYS)
    if eng.store is not None and eng.store.run_id == run_id:
        hist = eng.store.history(wanted)
        units = {k: s.unit for k, s in eng.store.sensors.items()}
        series = {
            k: ([t for t, _ in v], [x for _, x in v], units.get(k, "")) for k, v in hist.items()
        }
    else:
        series = recorded_series(eng, run_id)
    rows = []
    for k in wanted:
        if k not in series:
            continue
        ts, vs, unit = series[k]
        a = _range_stats(ts, vs, normal_from, normal_to)
        b = _range_stats(ts, vs, anom_from, anom_to)
        if a is None or b is None:
            continue
        shift = (b["mean"] - a["mean"]) / a["std"] if a["std"] > 1e-12 else 0.0
        ratio = b["std"] / a["std"] if a["std"] > 1e-12 else 0.0
        hf_ratio = b["hf_std"] / a["hf_std"] if a["hf_std"] > 1e-12 else 0.0
        deviation = max(
            abs(shift) / 3.0,
            abs(math.log(ratio)) if ratio > 0 else 3.0,
            abs(math.log(hf_ratio)) if hf_ratio > 0 else 3.0,  # 0 => flat-lined sensor
        )
        rows.append(
            {
                "sensor": k,
                "unit": unit,
                "normal": a,
                "abnormal": b,
                "mean_shift_sigma": round(shift, 3),
                "std_ratio": round(ratio, 3),
                "hf_ratio": round(hf_ratio, 3),
                "deviation_score": round(deviation, 3),
            }
        )
    rows.sort(key=lambda r: -r["deviation_score"])
    return {
        "run_id": run_id,
        "normal_range": [normal_from, normal_to],
        "abnormal_range": [anom_from, anom_to],
        "note": "통계적 차이일 뿐 고장 원인 진단이 아니다.",
        "sensors": rows,
    }
