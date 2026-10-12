"""Train the detector bundle with the repo's own code and export it to JSON for the web demo.

The browser demo (pages/) scores windows with the exported numbers, so its rule baseline,
Z-score detectors and Isolation Forest trees are exactly the ones trained here.

uv run python scripts/pages_demo/export_model.py --root .demo-data --workers 8
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from twinpilot_ai.anomaly.detectors import DetectorBundle
from twinpilot_ai.evaluation.metrics import evaluate_detector
from twinpilot_ai.features.windows import DEFAULT_SPEC, FAST_CHANNELS, SLOW_CHANNELS
from twinpilot_ai.training.make_dataset import make_dataset, plan
from twinpilot_ai.training.train import load_splits, train_bundle

FEATURE_SET = "residual"  # operational feature set (docs/eval/eval-v1.md, experiment 2)
PRIMARY = "zscore-tail"
OUT = Path("pages/model/detector-v1-residual.json")


def _arr(a: np.ndarray) -> list:
    return [float(x) for x in np.asarray(a, dtype=np.float64).ravel()]


def _tree(t: dict[str, np.ndarray]) -> dict[str, list]:
    feat = t["feature"].tolist()
    return {
        "f": feat,
        # thresholds only matter on internal nodes, leaf values only on leaves
        "t": [float(v) if f >= 0 else 0 for v, f in zip(t["threshold"], feat, strict=True)],
        "l": t["left"].tolist(),
        "r": t["right"].tolist(),
        "v": [float(v) if f < 0 else 0 for v, f in zip(t["leaf_value"], feat, strict=True)],
    }


def export(bundle: DetectorBundle) -> dict:
    dets = {}
    for key, d in bundle.detectors.items():
        e = {
            "model_version": d.model_version,
            "threshold": float(d.threshold),
            "mu": _arr(d._mu),
            "sd": _arr(d._sd),
        }
        if key == "threshold":
            e |= {
                "max_idx": d._max_idx.tolist(),
                "min_idx": d._min_idx.tolist(),
                "hi": _arr(d.hi),
                "lo": _arr(d.lo),
                "span": _arr(d.span),
            }
        elif key == "zscore-tail":
            e["tail"] = _arr(d.tail)
        elif key == "isolation-forest":
            e |= {
                "scaler_mean": _arr(d.scaler.mean_),
                "scaler_scale": _arr(d.scaler.scale_),
                "psi": int(d.model.psi),
                "trees": [_tree(t) for t in d.model.trees],
            }
        dets[key] = e
    return {
        "format": "twinpilot-detector-json-v1",
        "dataset_version": bundle.dataset_version,
        "feature_set": bundle.feature_set,
        "feature_names": bundle.feature_names,
        "primary": bundle.primary,
        "window": {
            "length_s": DEFAULT_SPEC.length_s,
            "stride_s": DEFAULT_SPEC.stride_s,
            "n_fast": DEFAULT_SPEC.n_fast,
            "n_slow": DEFAULT_SPEC.n_slow,
        },
        "fast_channels": list(FAST_CHANNELS),
        "slow_channels": list(SLOW_CHANNELS),
        "calibration": {
            k: (float(v) if isinstance(v, float) else v) for k, v in bundle.calibration.items()
        },
        "detectors": dets,
    }


def offline_eval(bundle: DetectorBundle, root: str, workers: int) -> dict:
    """Score the exported bundle on the dataset-v1 test split with the repo's metrics."""
    specs = [s for s in plan() if s.split == "test"]
    manifest = make_dataset("v1-test", root, workers, specs=specs)
    test = load_splits(manifest, ("test",), (FEATURE_SET,))[FEATURE_SET]
    out = {}
    for key, det in bundle.detectors.items():
        r = evaluate_detector(det, test)
        out[key] = {
            **{k: r["overall"][k] for k in ("precision", "recall", "f1", "fpr")},
            "run_detection_rate": r["run_detection_rate"],
            "median_delay_s": r["median_delay_s"],
            "false_alarms_per_hour": r["false_alarms_per_hour"],
            "by_fault_type": r["by_fault_type"],
        }
    return {"split": "test", "runs": len(test), "detectors": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".demo-data")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--n-estimators", type=int, default=200)
    ap.add_argument("--no-eval", action="store_true")
    a = ap.parse_args()
    # same normal train/val runs (seeds, speed scales) as dataset v1
    specs = [s for s in plan() if s.split in ("train", "val") and not s.faults]
    manifest = make_dataset("v1-normal", a.root, a.workers, specs=specs)
    data = load_splits(manifest, ("train", "val"), (FEATURE_SET,))[FEATURE_SET]
    train = [r for r in data if r.run_id in {s.run_id for s in specs if s.split == "train"}]
    val = [r for r in data if r.run_id in {s.run_id for s in specs if s.split == "val"}]
    bundle = train_bundle(train, val, "v1", FEATURE_SET, n_estimators=a.n_estimators)
    bundle.primary = PRIMARY
    doc = export(bundle)
    if not a.no_eval:
        doc["offline_eval"] = offline_eval(bundle, a.root, a.workers)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    bundle.save(Path(a.root) / "detector-v1-residual.joblib")
    print(f"train runs {len(train)}, val runs {len(val)}")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")
    print(bundle.calibration)
    for k, v in doc.get("offline_eval", {}).get("detectors", {}).items():
        print(k, {m: v[m] for m in ("precision", "recall", "f1", "fpr", "run_detection_rate")})


if __name__ == "__main__":
    main()
