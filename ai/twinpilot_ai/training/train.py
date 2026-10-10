"""Train detectors on normal windows and calibrate thresholds on held-out normal windows.

uv run python -m twinpilot_ai.training.train --manifest datasets/manifests/v1.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from twinpilot_ai.anomaly.detectors import (
    DETECTORS,
    DetectorBundle,
)
from twinpilot_ai.features.dataset import RunWindows, window_run_multi
from twinpilot_ai.training.make_dataset import load_manifest, run_dir_for

TARGET_FPR = 0.01
DEFAULT_MODEL = Path("ai/models/detector-v1.joblib")


def load_split(
    manifest_path: Path | str, splits: tuple[str, ...], feature_set: str = "full"
) -> list[RunWindows]:
    return load_splits(manifest_path, splits, (feature_set,))[feature_set]


def load_splits(
    manifest_path: Path | str, splits: tuple[str, ...], feature_sets: tuple[str, ...]
) -> dict[str, list[RunWindows]]:
    """Sequential read (pyarrow + threads segfaulted on Windows); one read per run."""
    m = load_manifest(manifest_path)
    out: dict[str, list[RunWindows]] = {fs: [] for fs in feature_sets}
    for r in m["runs"]:
        if r["split"] not in splits:
            continue
        multi = window_run_multi(
            run_dir_for(manifest_path, r["run_id"]), r["faults"], feature_sets, run_id=r["run_id"]
        )
        for fs in feature_sets:
            out[fs].append(multi[fs])
    return out


def stack(runs: list[RunWindows], normal_only: bool = False) -> np.ndarray:
    parts = []
    for r in runs:
        mask = (r.y == 0) & ~r.ignore if normal_only else np.ones(len(r.y), bool)
        parts.append(r.X[mask])
    return np.concatenate(parts)


def train_bundle(
    train: list[RunWindows],
    val: list[RunWindows],
    dataset_version: str,
    feature_set: str = "full",
    fpr: float = TARGET_FPR,
    detectors: tuple[str, ...] = tuple(DETECTORS),
    calibrate_rules: bool = True,
    n_estimators: int = 200,
) -> DetectorBundle:
    names = train[0].names
    X_train = stack(train, normal_only=True)
    X_val_normal = stack(val, normal_only=True)
    dets = {}
    calib = {
        "target_fpr": fpr,
        "n_train_windows": int(len(X_train)),
        "n_val_normal_windows": int(len(X_val_normal)),
    }
    for key in detectors:
        det = (
            DETECTORS[key](n_estimators=n_estimators)
            if key == "isolation-forest"
            else DETECTORS[key]()
        )
        det.fit(X_train, names)
        if key == "isolation-forest" or calibrate_rules:
            calib[f"{key}_default_threshold"] = det.threshold
            det.calibrate(X_val_normal, fpr)
        calib[f"{key}_threshold"] = det.threshold
        dets[key] = det
    return DetectorBundle(dataset_version, feature_set, names, dets, calibration=calib)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="datasets/manifests/v1.yaml")
    ap.add_argument("--feature-set", default="full")
    ap.add_argument("--out", default=str(DEFAULT_MODEL))
    a = ap.parse_args()
    m = load_manifest(a.manifest)
    train = load_split(a.manifest, ("train",), a.feature_set)
    val = load_split(a.manifest, ("val",), a.feature_set)
    bundle = train_bundle(train, val, m["dataset_version"], a.feature_set)
    path = bundle.save(a.out)
    print(f"saved {path} ({path.stat().st_size / 1e6:.2f} MB)")
    print(bundle.calibration)


if __name__ == "__main__":
    main()
