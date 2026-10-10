"""Evaluation metrics: window-level P/R/F1/FPR, run-level detection delay, breakdowns."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from twinpilot_ai.anomaly.detectors import Detector
from twinpilot_ai.features.dataset import RunWindows

ALARM_CONSECUTIVE = 2  # online alarm = 2 consecutive anomalous windows
WINDOW_STRIDE_S = 0.5


def _safe(a: float, b: float) -> float:
    return float(a / b) if b else 0.0


def confusion(y: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    precision = _safe(tp, tp + fp)
    recall = _safe(tp, tp + fn)
    f1 = _safe(2 * precision * recall, precision + recall)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "fpr": round(_safe(fp, fp + tn), 4),
        "miss_rate": round(_safe(fn, fn + tp), 4),
    }


def alarms(pred: np.ndarray, k: int = ALARM_CONSECUTIVE) -> np.ndarray:
    """Rising edges of k consecutive positive windows."""
    run = np.convolve(pred.astype(int), np.ones(k, dtype=int), mode="full")[: len(pred)]
    on = run >= k
    return on & ~np.concatenate([[False], on[:-1]])


def evaluate_detector(det: Detector, runs: list[RunWindows]) -> dict[str, Any]:
    rows = []
    delays = []
    false_alarms = 0
    normal_seconds = 0.0
    for r in runs:
        pred = det.predict(r.X).astype(int)
        keep = ~r.ignore
        rows.append(
            pd.DataFrame(
                {
                    "run_id": r.run_id,
                    "y": r.y[keep],
                    "pred": pred[keep],
                    "fault_type": r.fault_type[keep],
                    "severity": r.severity[keep],
                }
            )
        )
        al = alarms(pred)
        in_fault = np.zeros(len(r.y), bool)
        for f in r.faults:
            a, b = f["start_time"], f["start_time"] + f["duration"]
            win = (r.t_end >= a) & (r.t_end <= b + 2.0)
            in_fault |= win | ((r.t_end > b) & (r.t_end <= b + 10.0))
            hits = np.flatnonzero(win & (pred == 1))
            delays.append(
                {
                    "run_id": r.run_id,
                    "fault_type": f["type"],
                    "target": f["target"],
                    "severity": f["severity"],
                    "detected": bool(len(hits)),
                    "delay_s": round(float(r.t_end[hits[0]] - a), 2) if len(hits) else None,
                }
            )
        false_alarms += int((al & ~in_fault).sum())
        normal_seconds += float((~in_fault).sum()) * WINDOW_STRIDE_S
    df = pd.concat(rows, ignore_index=True)
    overall = confusion(df["y"].to_numpy(), df["pred"].to_numpy())
    dd = pd.DataFrame(delays)
    by_type, by_sev = {}, {}
    if len(dd):
        pos = df[df["y"] == 1]
        for t, g in pos.groupby("fault_type"):
            rt = dd[dd["fault_type"] == t]
            by_type[t] = {
                "window_recall": round(float(g["pred"].mean()), 4),
                "run_detection_rate": round(float(rt["detected"].mean()), 4),
                "median_delay_s": _median(rt["delay_s"]),
                "runs": int(len(rt)),
            }
        for s, g in pos.groupby("severity"):
            rs = dd[dd["severity"] == s]
            by_sev[f"{s:.1f}"] = {
                "window_recall": round(float(g["pred"].mean()), 4),
                "run_detection_rate": round(float(rs["detected"].mean()), 4),
                "median_delay_s": _median(rs["delay_s"]),
            }
    return {
        "model_version": det.model_version,
        "threshold": round(float(det.threshold), 5),
        "windows": int(len(df)),
        "overall": overall,
        "run_detection_rate": round(float(dd["detected"].mean()), 4) if len(dd) else None,
        "median_delay_s": _median(dd["delay_s"]) if len(dd) else None,
        "false_alarms_per_hour": round(_safe(false_alarms, normal_seconds / 3600.0), 2),
        "by_fault_type": by_type,
        "by_severity": by_sev,
        "detections": delays,
    }


def _median(s: pd.Series) -> float | None:
    s = s.dropna()
    return round(float(s.median()), 2) if len(s) else None
