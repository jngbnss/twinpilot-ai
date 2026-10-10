"""Small deterministic dataset + detector bundle for CI and backend tests.

Generated on the fly from fixed seeds (a few seconds), so no binary model has to be
committed and the fixture always matches the current feature code.
"""

from __future__ import annotations

from pathlib import Path

from twinpilot_ai.anomaly.detectors import DetectorBundle
from twinpilot_ai.training.make_dataset import RunSpec, make_dataset
from twinpilot_ai.training.train import load_splits, train_bundle

FIXTURE_FEATURE_SET = "residual"


def fixture_specs(duration: float = 60.0) -> list[RunSpec]:
    train = [RunSpec(f"fx-train-{i}", "train", 300 + i, duration=duration) for i in range(6)]
    val = [RunSpec(f"fx-val-{i}", "val", 400 + i, duration=duration) for i in range(3)]
    return train + val


def build_fixture_bundle(root: Path, n_estimators: int = 50) -> DetectorBundle:
    manifest = make_dataset(
        "fx", root, workers=1, specs=fixture_specs(), manifest_dir=root / "manifests"
    )
    data = load_splits(manifest, ("train", "val"), (FIXTURE_FEATURE_SET,))[FIXTURE_FEATURE_SET]
    train = [r for r in data if r.run_id.startswith("fx-train")]
    val = [r for r in data if r.run_id.startswith("fx-val")]
    bundle = train_bundle(train, val, "fx", FIXTURE_FEATURE_SET, n_estimators=n_estimators)
    bundle.primary = "zscore-tail"
    return bundle
