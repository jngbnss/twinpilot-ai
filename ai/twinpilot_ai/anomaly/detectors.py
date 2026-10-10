"""Anomaly detectors over window features. Rule baselines first, then Isolation Forest.

All detectors are trained on NORMAL windows only. Their output is a score plus the
features that deviate most from normal; it is not a diagnosis of the root cause.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from twinpilot_ai.anomaly.iforest import NumpyIsolationForest, Standardizer
from twinpilot_ai.features.windows import sensor_of

STD_FLOOR = 1e-6


class Detector:
    name = "base"
    version = "0"

    def __init__(self) -> None:
        self.threshold = 0.0
        self.feature_names: list[str] = []
        self._mu: np.ndarray | None = None
        self._sd: np.ndarray | None = None

    @property
    def model_version(self) -> str:
        return f"{self.name}-v{self.version}"

    def fit(self, X: np.ndarray, names: list[str]) -> Detector:
        self.feature_names = list(names)
        self._mu = X.mean(axis=0)
        self._sd = np.maximum(X.std(axis=0), STD_FLOOR)
        self._fit(X)
        return self

    def _fit(self, X: np.ndarray) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def score(self, X: np.ndarray) -> np.ndarray:  # pragma: no cover - abstract
        raise NotImplementedError

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.score(X) > self.threshold

    def calibrate(self, X_normal: np.ndarray, fpr: float = 0.01) -> float:
        """Threshold = (1 - fpr) quantile of scores on held-out normal windows."""
        self.threshold = float(np.quantile(self.score(X_normal), 1.0 - fpr))
        return self.threshold

    def zscores(self, X: np.ndarray) -> np.ndarray:
        return (X - self._mu) / self._sd

    def explain(self, x: np.ndarray, k: int = 3) -> list[str]:
        """Sensors whose features deviate most from the normal distribution."""
        z = np.abs(self.zscores(x.reshape(1, -1))[0])
        out: list[str] = []
        for i in np.argsort(-z):
            s = sensor_of(self.feature_names[i])
            if s not in out:
                out.append(s)
            if len(out) == k:
                break
        return out


class ThresholdDetector(Detector):
    """Fixed per-channel limits: p0.5 / p99.5 of window min / max on normal data."""

    name = "threshold"
    version = "1"

    def __init__(self, q: float = 0.995):
        super().__init__()
        self.q = q
        self.threshold = 0.0

    def _fit(self, X: np.ndarray) -> None:
        names = self.feature_names
        mx = [i for i, n in enumerate(names) if n.endswith(":max")]
        if mx:
            self._max_idx = np.array(mx, dtype=int)
            self._min_idx = np.array(
                [names.index(names[i].replace(":max", ":min")) for i in mx], dtype=int
            )
        else:  # feature sets without min/max: limit every feature on both sides
            self._max_idx = self._min_idx = np.arange(len(names), dtype=int)
        self.hi = np.quantile(X[:, self._max_idx], self.q, axis=0)
        self.lo = np.quantile(X[:, self._min_idx], 1 - self.q, axis=0)
        self.span = np.maximum(self.hi - self.lo, STD_FLOOR)

    def exceedance(self, X: np.ndarray) -> np.ndarray:
        over = (X[:, self._max_idx] - self.hi) / self.span
        under = (self.lo - X[:, self._min_idx]) / self.span
        return np.maximum(np.maximum(over, under), 0.0)

    def score(self, X: np.ndarray) -> np.ndarray:
        return self.exceedance(X).max(axis=1)

    def explain(self, x: np.ndarray, k: int = 3) -> list[str]:
        e = self.exceedance(x.reshape(1, -1))[0]
        if e.max() <= 0:
            return super().explain(x, k)
        order = np.argsort(-e)[:k]
        return [sensor_of(self.feature_names[self._max_idx[i]]) for i in order if e[i] > 0]


class ZScoreDetector(Detector):
    """Max |z| over window features relative to normal windows."""

    name = "zscore"
    version = "1"

    def __init__(self, threshold: float = 4.0):
        super().__init__()
        self.threshold = threshold

    def _fit(self, X: np.ndarray) -> None:
        pass

    def score(self, X: np.ndarray) -> np.ndarray:
        return np.abs(self.zscores(X)).max(axis=1)


class TailZDetector(Detector):
    """Z-score normalized per feature by its own normal tail (p99.9 of |z|).

    Plain max-|z| is dominated by heavy-tailed features; dividing each |z| by that
    feature's normal tail makes every feature equally likely to fire on normal data.
    """

    name = "zscore-tail"
    version = "1"

    def __init__(self, q: float = 0.999):
        super().__init__()
        self.q = q
        self.threshold = 1.0

    def _fit(self, X: np.ndarray) -> None:
        self.tail = np.maximum(np.quantile(np.abs(self.zscores(X)), self.q, axis=0), 1.0)

    def normalized(self, X: np.ndarray) -> np.ndarray:
        return np.abs(self.zscores(X)) / self.tail

    def score(self, X: np.ndarray) -> np.ndarray:
        return self.normalized(X).max(axis=1)

    def explain(self, x: np.ndarray, k: int = 3) -> list[str]:
        z = self.normalized(x.reshape(1, -1))[0]
        out: list[str] = []
        for i in np.argsort(-z):
            s = sensor_of(self.feature_names[i])
            if s not in out:
                out.append(s)
            if len(out) == k:
                break
        return out


class IsolationForestDetector(Detector):
    name = "isolation-forest"
    version = "1"

    def __init__(self, n_estimators: int = 200, random_state: int = 0, max_samples: int = 256):
        super().__init__()
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.max_samples = max_samples

    def _fit(self, X: np.ndarray) -> None:
        self.scaler = Standardizer().fit(X)
        self.model = NumpyIsolationForest(
            n_estimators=self.n_estimators,
            max_samples=self.max_samples,
            random_state=self.random_state,
        ).fit(self.scaler.transform(X))

    def score(self, X: np.ndarray) -> np.ndarray:
        # Liu et al. anomaly score in (0, 1]; higher = more anomalous
        return self.model.score_samples(self.scaler.transform(X))


DETECTORS = {
    "threshold": ThresholdDetector,
    "zscore": ZScoreDetector,
    "zscore-tail": TailZDetector,
    "isolation-forest": IsolationForestDetector,
}


@dataclass
class DetectorBundle:
    """Everything the online service needs: detectors + feature contract."""

    dataset_version: str
    feature_set: str
    feature_names: list[str]
    detectors: dict[str, Detector]
    primary: str = "isolation-forest"
    calibration: dict[str, Any] = field(default_factory=dict)

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path, compress=3)
        return path

    @staticmethod
    def load(path: Path | str) -> DetectorBundle:
        return joblib.load(path)

    @property
    def model_version(self) -> str:
        return f"{self.detectors[self.primary].model_version}+{self.dataset_version}"
