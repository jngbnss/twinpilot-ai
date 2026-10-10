"""Isolation Forest in pure NumPy (Liu, Ting & Zhou, 2008).

Implemented in-repo because the local Windows Smart App Control blocks the compiled
scikit-learn / SciPy extension modules on the development machine (decision D-016).
The algorithm follows the original paper: random sub-sampling (psi), random feature,
uniform random split, height limit ceil(log2 psi), anomaly score s = 2^(-E[h(x)] / c(psi)).
"""

from __future__ import annotations

import math

import numpy as np

EULER = 0.5772156649015329


def c_factor(n: np.ndarray | int) -> np.ndarray:
    """Average path length of an unsuccessful BST search over n points."""
    n = np.asarray(n, dtype=float)
    out = np.zeros_like(n)
    big = n > 2
    out[big] = 2.0 * (np.log(n[big] - 1.0) + EULER) - 2.0 * (n[big] - 1.0) / n[big]
    out[n == 2] = 1.0
    return out


class _Tree:
    __slots__ = ("feature", "threshold", "left", "right", "size", "depth")

    def __init__(self) -> None:
        self.feature: list[int] = []
        self.threshold: list[float] = []
        self.left: list[int] = []
        self.right: list[int] = []
        self.size: list[int] = []
        self.depth: list[int] = []

    def add(self, depth: int) -> int:
        self.feature.append(-1)
        self.threshold.append(0.0)
        self.left.append(-1)
        self.right.append(-1)
        self.size.append(0)
        self.depth.append(depth)
        return len(self.feature) - 1

    def freeze(self) -> dict[str, np.ndarray]:
        return {
            "feature": np.array(self.feature, dtype=np.int32),
            "threshold": np.array(self.threshold, dtype=np.float64),
            "left": np.array(self.left, dtype=np.int32),
            "right": np.array(self.right, dtype=np.int32),
            "leaf_value": np.array(self.depth, dtype=np.float64) + c_factor(np.array(self.size)),
        }


def _build(X: np.ndarray, rng: np.random.Generator, height_limit: int) -> dict[str, np.ndarray]:
    tree = _Tree()
    root = tree.add(0)
    stack = [(root, np.arange(len(X)))]
    while stack:
        node, idx = stack.pop()
        depth = tree.depth[node]
        tree.size[node] = len(idx)
        if depth >= height_limit or len(idx) <= 1:
            continue
        sub = X[idx]
        lo, hi = sub.min(axis=0), sub.max(axis=0)
        candidates = np.flatnonzero(hi > lo)
        if len(candidates) == 0:
            continue
        f = int(rng.choice(candidates))
        t = float(rng.uniform(lo[f], hi[f]))
        go_left = sub[:, f] < t
        left, right = tree.add(depth + 1), tree.add(depth + 1)
        tree.feature[node], tree.threshold[node] = f, t
        tree.left[node], tree.right[node] = left, right
        stack.append((left, idx[go_left]))
        stack.append((right, idx[~go_left]))
    return tree.freeze()


class NumpyIsolationForest:
    def __init__(self, n_estimators: int = 200, max_samples: int = 256, random_state: int = 0):
        self.n_estimators = n_estimators
        self.max_samples = max_samples
        self.random_state = random_state
        self.trees: list[dict[str, np.ndarray]] = []
        self.psi = 0

    def fit(self, X: np.ndarray) -> NumpyIsolationForest:
        X = np.asarray(X, dtype=np.float64)
        rng = np.random.Generator(np.random.PCG64(self.random_state))
        self.psi = min(self.max_samples, len(X))
        height_limit = math.ceil(math.log2(max(self.psi, 2)))
        self.trees = []
        for _ in range(self.n_estimators):
            idx = rng.choice(len(X), size=self.psi, replace=False)
            self.trees.append(_build(X[idx], rng, height_limit))
        return self

    def path_lengths(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        rows = np.arange(len(X))
        total = np.zeros(len(X))
        for t in self.trees:
            node = np.zeros(len(X), dtype=np.int32)
            while True:
                f = t["feature"][node]
                internal = f >= 0
                if not internal.any():
                    break
                ni = node[internal]
                go_left = X[rows[internal], f[internal]] < t["threshold"][ni]
                node[internal] = np.where(go_left, t["left"][ni], t["right"][ni])
            total += t["leaf_value"][node]
        return total / len(self.trees)

    def score_samples(self, X: np.ndarray) -> np.ndarray:
        """Anomaly score s in (0, 1]; higher = more anomalous (paper definition)."""
        return np.power(2.0, -self.path_lengths(X) / float(c_factor(self.psi)))


class Standardizer:
    def fit(self, X: np.ndarray) -> Standardizer:
        self.mean_ = X.mean(axis=0)
        self.scale_ = np.where(X.std(axis=0) > 1e-12, X.std(axis=0), 1.0)
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean_) / self.scale_
