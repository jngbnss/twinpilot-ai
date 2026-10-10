"""Offline window extraction + labels from recorded runs (Parquet)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from numpy.lib.stride_tricks import sliding_window_view

from twinpilot_ai.features.windows import (
    DEFAULT_SPEC,
    FAST_CHANNELS,
    SLOW_CHANNELS,
    WindowSpec,
    features_batch,
)

LABEL_MIN_OVERLAP = 0.5  # fraction of the window inside a fault interval
POST_FAULT_GRACE_S = 10.0  # windows right after a fault are excluded from evaluation


@dataclass
class RunWindows:
    run_id: str
    X: np.ndarray
    t_end: np.ndarray
    y: np.ndarray
    ignore: np.ndarray
    fault_type: np.ndarray  # str per window ("" for normal)
    severity: np.ndarray
    names: list[str]
    faults: list[dict]


def load_arrays(run_dir: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fast matrix (N, C_fast) with times, slow matrix (M, C_slow) with times."""
    table = pq.read_table(
        run_dir / "telemetry.parquet", columns=["simulation_time", "source", "metric", "value"]
    )
    df = table.to_pandas()
    df["key"] = df["source"] + "." + df["metric"]

    def matrix(keys: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
        sub = df[df["key"].isin(keys)]
        wide = sub.pivot_table(
            index="simulation_time", columns="key", values="value", aggfunc="last"
        )
        wide = wide.reindex(columns=list(keys)).sort_index().ffill().bfill()
        return wide.index.to_numpy(), wide.to_numpy()

    tf, F = matrix(FAST_CHANNELS)
    ts, S = matrix(SLOW_CHANNELS)
    return tf, F, ts, S


def window_run_multi(
    run_dir: Path,
    faults: list[dict],
    feature_sets: tuple[str, ...] = ("full",),
    spec: WindowSpec = DEFAULT_SPEC,
    run_id: str | None = None,
) -> dict[str, RunWindows]:
    """Read a run once and compute several feature sets on identical windows."""
    tf, F, ts, S = load_arrays(run_dir)
    nf, ns = spec.n_fast, spec.n_slow
    fast_w = sliding_window_view(F, nf, axis=0).transpose(0, 2, 1)  # (N-nf+1, nf, C)
    slow_w = sliding_window_view(S, ns, axis=0).transpose(0, 2, 1)
    t_ends = np.arange(spec.length_s, tf[-1] + 1e-9, spec.stride_s)
    fi = np.searchsorted(tf, t_ends + 1e-9) - nf  # window start index (end inclusive)
    si = np.searchsorted(ts, t_ends + 1e-9) - ns
    ok = (fi >= 0) & (si >= 0) & (fi < len(fast_w)) & (si < len(slow_w))
    t_ends, fi, si = t_ends[ok], fi[ok], si[ok]
    fast_sel, slow_sel = fast_w[fi], slow_w[si]

    y = np.zeros(len(t_ends), dtype=int)
    ignore = np.zeros(len(t_ends), dtype=bool)
    ftype = np.array([""] * len(t_ends), dtype=object)
    sev = np.zeros(len(t_ends))
    t_starts = t_ends - spec.length_s
    for f in faults:
        a, b = f["start_time"], f["start_time"] + f["duration"]
        overlap = np.clip(np.minimum(t_ends, b) - np.maximum(t_starts, a), 0, None)
        hit = overlap / spec.length_s >= LABEL_MIN_OVERLAP
        y[hit] = 1
        ftype[hit] = f["type"]
        sev[hit] = np.maximum(sev[hit], f["severity"])
        partial = (overlap > 0) & ~hit
        grace = (t_starts >= b) & (t_starts < b + POST_FAULT_GRACE_S)
        ignore |= (partial | grace) & (y == 0)
    out = {}
    for fs in feature_sets:
        X, names = features_batch(fast_sel, slow_sel, fs, spec)
        out[fs] = RunWindows(
            run_id or run_dir.name, X, t_ends, y, ignore, ftype, sev, names, faults
        )
    return out


def window_run(
    run_dir: Path,
    faults: list[dict],
    feature_set: str = "full",
    spec: WindowSpec = DEFAULT_SPEC,
    run_id: str | None = None,
) -> RunWindows:
    return window_run_multi(run_dir, faults, (feature_set,), spec, run_id)[feature_set]


def windows_frame(runs: list[RunWindows]) -> pd.DataFrame:
    """Per-window metadata (without features) for evaluation tables."""
    rows = []
    for r in runs:
        rows.append(
            pd.DataFrame(
                {
                    "run_id": r.run_id,
                    "t_end": r.t_end,
                    "y": r.y,
                    "ignore": r.ignore,
                    "fault_type": r.fault_type,
                    "severity": r.severity,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)
