import numpy as np
import pytest

from twinpilot_ai.anomaly.detectors import (
    DetectorBundle,
    IsolationForestDetector,
    TailZDetector,
    ThresholdDetector,
    ZScoreDetector,
)
from twinpilot_ai.anomaly.iforest import NumpyIsolationForest, c_factor

CHANNELS = ["a.x", "b.y", "c.z"]
NAMES = [f"{c}:{s}" for s in ("mean", "min", "max", "std") for c in CHANNELS]


def data(n=600, seed=0):
    rng = np.random.default_rng(seed)
    mean = rng.normal(size=(n, 3))
    std = np.abs(rng.normal(1.0, 0.1, size=(n, 3)))
    X = np.concatenate([mean, mean - 2 * std, mean + 2 * std, std], axis=1)
    return X


def anomalies(n=50):
    """Channel b.y shifted strongly; a.x mildly (IF is weak on single-feature shifts)."""
    X = data(n, seed=9)
    for stat in ("mean", "min", "max"):
        X[:, NAMES.index(f"b.y:{stat}")] += 6
        X[:, NAMES.index(f"a.x:{stat}")] += 3
    X[:, NAMES.index("b.y:std")] += 1.0
    return X


@pytest.mark.parametrize(
    "cls", [ThresholdDetector, ZScoreDetector, TailZDetector, IsolationForestDetector]
)
def test_detector_separates_shifted_windows(cls):
    det = cls().fit(data(), NAMES)
    det.calibrate(data(400, seed=1), fpr=0.01)
    normal_fpr = det.predict(data(400, seed=2)).mean()
    recall = det.predict(anomalies()).mean()
    assert normal_fpr < 0.05
    assert recall > 0.9
    assert "b.y" in det.explain(anomalies()[0])
    assert det.model_version.endswith("-v1")


def test_c_factor_reference_values():
    assert c_factor(2) == 1.0
    assert c_factor(256) == pytest.approx(10.2448, abs=1e-3)


def test_iforest_deterministic_and_scores_outliers_higher():
    X = data()
    a = NumpyIsolationForest(50, 128, random_state=3).fit(X)
    b = NumpyIsolationForest(50, 128, random_state=3).fit(X)
    assert np.array_equal(a.score_samples(X[:20]), b.score_samples(X[:20]))
    s_norm = a.score_samples(data(100, seed=5)).mean()
    s_anom = a.score_samples(data(100, seed=5) + 8).mean()
    assert 0 < s_norm < 0.6 < s_anom <= 1.0


def test_iforest_agrees_with_sklearn_reference():
    sk = pytest.importorskip("sklearn.ensemble")
    X = data()
    probe = np.concatenate([data(200, seed=7), anomalies()])
    ours = NumpyIsolationForest(200, 256, random_state=0).fit(X).score_samples(probe)
    ref = (
        -sk.IsolationForest(n_estimators=200, max_samples=256, random_state=0)
        .fit(X)
        .score_samples(probe)
    )
    rank = lambda v: np.argsort(np.argsort(v))  # noqa: E731
    assert np.corrcoef(rank(ours), rank(ref))[0, 1] > 0.85


def test_bundle_roundtrip(tmp_path):
    det = IsolationForestDetector(n_estimators=20).fit(data(), NAMES)
    det.calibrate(data(200, seed=1))
    b = DetectorBundle("vt", "full", NAMES, {"isolation-forest": det})
    path = b.save(tmp_path / "m.joblib")
    b2 = DetectorBundle.load(path)
    x = anomalies()[:5]
    assert np.array_equal(b2.detectors["isolation-forest"].score(x), det.score(x))
    assert b2.model_version == "isolation-forest-v1+vt"
