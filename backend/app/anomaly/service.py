"""Online anomaly detection over the Twin State Store (read-only).

Every 0.5 s of simulation time the latest 2 s window is featurized with the same code
as offline training and scored by every detector in the bundle. The result only
populates ``anomaly_state`` and timeline events — it never reaches the safety layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import Settings
from app.twin.store import TwinStateStore
from twinpilot_ai.anomaly.detectors import DetectorBundle
from twinpilot_ai.features.windows import (
    DEFAULT_SPEC,
    FAST_CHANNELS,
    SLOW_CHANNELS,
    features_batch,
)

STRIDE_S = 0.5
ALARM_CONSECUTIVE = 2
MESSAGE = "{sensors}에서 평소와 다른 패턴이 감지되었습니다. 원인 진단은 별도 확인이 필요합니다."


class AnomalyService:
    def __init__(self, bundle: DetectorBundle | None, source: str = ""):
        self.bundle = bundle
        self.source = source
        self.reset("")

    @classmethod
    def from_settings(cls, settings: Settings) -> AnomalyService:
        path = Path(settings.model_path)
        if path.exists():
            return cls(DetectorBundle.load(path), str(path))
        return cls(None, str(path))

    def reset(self, run_id: str) -> None:
        self.run_id = run_id
        self._last_t = -1e9
        self._streak = 0
        self._alarm = False
        self.state: dict[str, Any] = self._idle_state()

    def _idle_state(self) -> dict[str, Any]:
        if self.bundle is None:
            return {"status": "model_not_loaded", "model_path": self.source}
        return {"status": "warming_up", "model_version": self.bundle.model_version}

    def _window(self, store: TwinStateStore) -> tuple[np.ndarray, np.ndarray] | None:
        nf, ns = DEFAULT_SPEC.n_fast, DEFAULT_SPEC.n_slow
        hist = store.history(list(FAST_CHANNELS) + list(SLOW_CHANNELS))
        fast, slow = [], []
        for k in FAST_CHANNELS:
            pts = hist[k]
            if len(pts) < nf:
                return None
            fast.append([v for _, v in pts[-nf:]])
        for k in SLOW_CHANNELS:
            pts = hist[k]
            if len(pts) < ns:
                return None
            slow.append([v for _, v in pts[-ns:]])
        return np.array(fast).T[None], np.array(slow).T[None]

    def update(self, store: TwinStateStore) -> list[dict[str, Any]]:
        if self.bundle is None:
            store.anomaly_state = self.state
            return []
        t = store.simulation_time
        if t - self._last_t < STRIDE_S - 1e-9:
            return []
        self._last_t = t
        win = self._window(store)
        if win is None:
            store.anomaly_state = self.state
            return []
        X, _ = features_batch(win[0], win[1], self.bundle.feature_set)
        b = self.bundle
        results = {}
        for key, det in b.detectors.items():
            score = float(det.score(X)[0])
            results[key] = {
                "score": round(score, 4),
                "threshold": round(float(det.threshold), 4),
                "is_anomaly": bool(score > det.threshold),
                "model_version": det.model_version,
            }
        primary = b.detectors[b.primary]
        p = results[b.primary]
        affected = primary.explain(X[0]) if p["is_anomaly"] else []
        self._streak = self._streak + 1 if p["is_anomaly"] else 0
        events: list[dict[str, Any]] = []
        alarm = self._streak >= ALARM_CONSECUTIVE
        if alarm and not self._alarm:
            events.append(
                {
                    "kind": "anomaly",
                    "time": round(t, 3),
                    "model_version": b.model_version,
                    "score": p["score"],
                    "threshold": p["threshold"],
                    "affected_sensors": affected,
                    "message": MESSAGE.format(sensors=", ".join(affected)),
                }
            )
        elif not alarm and self._alarm and self._streak == 0:
            events.append({"kind": "anomaly_cleared", "time": round(t, 3)})
        self._alarm = alarm
        self.state = {
            "status": "active",
            "timestamp": round(t, 3),
            "is_anomaly": p["is_anomaly"],
            "alarm": alarm,
            "anomaly_score": p["score"],
            "threshold": p["threshold"],
            "normalized_score": round(p["score"] / p["threshold"], 4) if p["threshold"] else 0.0,
            "affected_sensors": affected,
            "message": MESSAGE.format(sensors=", ".join(affected)) if affected else "",
            "model_version": b.model_version,
            "primary": b.primary,
            "feature_set": b.feature_set,
            "detectors": results,
        }
        store.anomaly_state = self.state
        return events
