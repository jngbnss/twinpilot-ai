"""Smoke test: inject a fault into a live autopilot run and watch anomaly_state change.

Requires a running backend with a trained model (ai/models/detector-v1.joblib).
uv run python scripts/anomaly_smoke.py [--base http://127.0.0.1:8770]
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8770")
    ap.add_argument("--fault", default="JOINT_LAG")
    ap.add_argument("--target", default="bucket_joint")
    ap.add_argument("--severity", type=float, default=0.9)
    a = ap.parse_args()
    c = httpx.Client(base_url=a.base, timeout=30)
    run_id = c.post("/api/runs", json={"mode": "live", "seed": 3, "autopilot": True}).json()[
        "run_id"
    ]
    print("live autopilot run:", run_id)
    time.sleep(12)
    st = c.get("/api/twin/state").json()["anomaly_state"]
    print(
        "before fault:", {k: st.get(k) for k in ("status", "alarm", "anomaly_score", "threshold")}
    )
    if st.get("status") != "active":
        print("FAIL anomaly service not active (model missing?)", st)
        sys.exit(1)
    f = c.post(
        "/api/faults",
        json={"type": a.fault, "target": a.target, "severity": a.severity, "duration": 20},
    ).json()
    print(f"fault {f['type']} on {f['target']} at t={f['start_time']}")
    alarm_at = None
    for _ in range(40):
        time.sleep(0.5)
        snap = c.get("/api/twin/state").json()
        st = snap["anomaly_state"]
        if st.get("alarm"):
            alarm_at = snap["simulation_time"]
            break
    report = c.post("/api/runs/stop").json()["report"]
    if alarm_at is None:
        print("FAIL no alarm within 20 s")
        sys.exit(1)
    print(f"alarm at t={alarm_at} (delay {alarm_at - f['start_time']:.2f} s)")
    print("affected:", st["affected_sensors"])
    print("message :", st["message"])
    print("detectors:", {k: v["is_anomaly"] for k, v in st["detectors"].items()})
    print("report fault_to_detection_s:", report["fault_to_detection_s"])
    print("SMOKE OK")


if __name__ == "__main__":
    main()
