import numpy as np
import pytest

from twinpilot_ai.features.windows import (
    FAST_CHANNELS,
    FEATURE_SETS,
    SLOW_CHANNELS,
    WindowSpec,
    features_batch,
    sensor_of,
)

SPEC = WindowSpec()


def windows(n=3, seed=0):
    rng = np.random.default_rng(seed)
    fast = rng.normal(size=(n, SPEC.n_fast, len(FAST_CHANNELS)))
    slow = rng.normal(size=(n, SPEC.n_slow, len(SLOW_CHANNELS)))
    return fast, slow


def test_window_sizes():
    assert SPEC.n_fast == 50 and SPEC.n_slow == 10


@pytest.mark.parametrize("fs", FEATURE_SETS)
def test_feature_sets_shapes_and_names(fs):
    fast, slow = windows()
    X, names = features_batch(fast, slow, fs)
    assert X.shape == (3, len(names))
    assert len(set(names)) == len(names)
    assert all(":" in n for n in names)


def test_feature_sets_are_nested():
    fast, slow = windows()
    prev: set[str] = set()
    for fs in FEATURE_SETS:
        names = set(features_batch(fast, slow, fs)[1])
        assert prev <= names
        prev = names


def test_batch_equals_single_window():
    """Online service computes one window at a time; must equal the batched result."""
    fast, slow = windows(5)
    X, _ = features_batch(fast, slow)
    for i in range(5):
        Xi, _ = features_batch(fast[i : i + 1], slow[i : i + 1])
        assert np.allclose(X[i], Xi[0])


def test_known_values():
    fast = np.zeros((1, 50, len(FAST_CHANNELS)))
    slow = np.zeros((1, 10, len(SLOW_CHANNELS)))
    i = FAST_CHANNELS.index("boom_joint.position")
    fast[0, :, i] = np.linspace(0, 1, 50)
    j = SLOW_CHANNELS.index("hydraulic.oil_temperature")
    slow[0, :, j] = 50.0
    X, names = features_batch(fast, slow, "full")
    f = dict(zip(names, X[0], strict=True))
    assert f["boom_joint.position:slope"] == pytest.approx(0.5)
    assert f["boom_joint.position:max"] == 1.0
    assert f["hydraulic.oil_temperature:mean"] == 50.0
    assert f["hydraulic.oil_temperature:std"] == 0.0
    # position moved 1 rad with zero velocity reported -> kinematic residual 1
    assert f["boom_joint.position:kin_residual"] == pytest.approx(1.0)
    assert sensor_of("boom_joint.position:kin_residual") == "boom_joint.position"
