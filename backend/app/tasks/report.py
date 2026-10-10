"""Task performance report. The score is always shown together with the raw metrics."""

from __future__ import annotations

from typing import Any

PENALTIES = {
    "collisions": 10,
    "worker_collisions": 25,
    "worker_proximity_entries": 5,
    "forbidden_entries": 10,
    "tipover_risks": 10,
    "drops": 5,
}


def score(metrics: dict[str, Any], time_limit_s: float) -> dict[str, float]:
    target = max(1, int(metrics.get("target_count", 3)))
    completion = 40.0 * min(1.0, metrics.get("delivered", 0) / target)
    t = metrics.get("completion_time", -1.0)
    time_pts = 20.0 * max(0.0, 1.0 - t / time_limit_s) if t and t > 0 else 0.0
    safety = 40.0
    deductions = {}
    for k, p in PENALTIES.items():
        n = int(metrics.get(k, 0))
        if n:
            deductions[k] = p * n
            safety -= p * n
    safety = max(0.0, safety)
    return {
        "total": round(completion + time_pts + safety, 1),
        "completion": round(completion, 1),
        "time": round(time_pts, 1),
        "safety": round(safety, 1),
        "deductions": deductions,
    }


def build_report(
    run_id: str,
    metrics: dict[str, Any],
    events: list[dict[str, Any]],
    time_limit_s: float,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    faults = [e for e in events if e.get("kind") == "fault"]
    safety = [e for e in events if e.get("kind") == "safety"]
    anomalies = [e for e in events if e.get("kind") == "anomaly"]
    world = [e for e in events if e.get("kind") == "world"]
    first_fault = min((f["start_time"] for f in faults), default=None)
    response = None
    if first_fault is not None:
        after = [a["time"] for a in anomalies if a["time"] >= first_fault]
        response = round(after[0] - first_fault, 3) if after else None
    return {
        "run_id": run_id,
        "level": "L1-simulation",
        "note": "All values come from a simulation (SIMULATED SENSOR). Not a field safety record.",
        "raw_metrics": metrics,
        "score": score(metrics, time_limit_s),
        "faults": faults,
        "safety_events": safety,
        "anomaly_events": anomalies,
        "world_events": world,
        "fault_to_detection_s": response,
        "meta": meta or {},
    }
