"""Reference outputs from the Python code for the web demo parity check (check_demo.mjs).

1. detectors: windows cut like backend/app/anomaly/service.py from runs of the
   evals/anomaly-cases.yaml cases, with features and every detector score from Python.
2. sim: noise-free trajectories of the Python simulator + safety layer driven by the
   scripted operator (with joint faults) and by a keyboard sequence that triggers the
   safety rules. The JS port must reproduce them.

uv run python scripts/pages_demo/make_fixture.py --model .demo-data/detector-v1-residual.joblib \
    --out .demo-data/fixture.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import yaml

from twinpilot_ai.anomaly.detectors import DetectorBundle
from twinpilot_ai.features.windows import DEFAULT_SPEC, FAST_CHANNELS, SLOW_CHANNELS, features_batch
from twinpilot_sim.control.mapper import keyboard_to_command
from twinpilot_sim.operator import OperatorConfig
from twinpilot_sim.params import ExcavatorParams, default_params
from twinpilot_sim.schema import JOINTS, FaultSpec
from twinpilot_sim.session import SimSession, make_operator

CASES = Path("evals/anomaly-cases.yaml")


def detector_case(bundle: DetectorBundle, case: dict) -> dict:
    faults = [FaultSpec.model_validate(case["fault"])] if case["fault"] else []
    session = SimSession(case["id"], seed=case["seed"], faults=faults)
    op = make_operator(session, OperatorConfig(loop=True))
    hist: dict[str, list[float]] = {k: [] for k in (*FAST_CHANNELS, *SLOW_CHANNELS)}
    last_t, windows = -1e9, []
    nf, ns = DEFAULT_SPEC.n_fast, DEFAULT_SPEC.n_slow
    while session.sim_time < case["duration"] - 1e-9:
        session.submit(op.act(session.ground_truth()))
        step = session.step()
        for r in step.records:
            if r.key in hist:
                hist[r.key].append(r.value)
        t = session.sim_time
        if t - last_t < 0.5 - 1e-9:
            continue
        last_t = t
        if any(len(hist[k]) < nf for k in FAST_CHANNELS) or any(
            len(hist[k]) < ns for k in SLOW_CHANNELS
        ):
            continue
        fast = np.array([hist[k][-nf:] for k in FAST_CHANNELS]).T[None]
        slow = np.array([hist[k][-ns:] for k in SLOW_CHANNELS]).T[None]
        X, _ = features_batch(fast, slow, bundle.feature_set)
        scores = {k: float(d.score(X)[0]) for k, d in bundle.detectors.items()}
        primary = bundle.detectors[bundle.primary]
        windows.append(
            {
                "t": round(t, 3),
                "fast": {k: hist[k][-nf:] for k in FAST_CHANNELS},
                "slow": {k: hist[k][-ns:] for k in SLOW_CHANNELS},
                "x": X[0].tolist(),
                "scores": scores,
                "explain": primary.explain(X[0]),
            }
        )
    return {"id": case["id"], "fault": case["fault"], "windows": windows}


def quiet_params() -> ExcavatorParams:
    d = default_params().model_dump()
    d["sensors"]["noise"] = dict.fromkeys(d["sensors"]["noise"], 0.0)
    return ExcavatorParams.model_validate(d)


def _state(session: SimSession, decision) -> dict:
    gt = session.ground_truth()
    sim = session.sim
    return {
        "t": round(session.sim_time, 6),
        "x": gt.base_x,
        "y": gt.base_y,
        "yaw": gt.yaw,
        "roll": gt.roll,
        "pitch": gt.pitch,
        "q": [gt.joints[j].position for j in JOINTS],
        "qd": [gt.joints[j].velocity for j in JOINTS],
        "effort": [gt.joints[j].effort for j in JOINTS],
        "temp": sim.temp,
        "energy": sim.energy_kj,
        "delivered": sim.world.counters.delivered,
        "payload": gt.payload_id,
        "worker_distance": gt.worker_distance,
        "rules": list(decision.rules),
        "level": int(decision.level),
    }


def sim_operator_trace(duration: float = 90.0) -> dict:
    faults = [
        FaultSpec(
            fault_id="f1",
            type="JOINT_LAG",
            target="bucket_joint",
            start_time=20,
            duration=20,
            severity=0.8,
        ),
        FaultSpec(
            fault_id="f2",
            type="FRICTION_INCREASE",
            target="boom_joint",
            start_time=45,
            duration=20,
            severity=0.6,
        ),
        FaultSpec(
            fault_id="f3",
            type="TEMP_RISE",
            target="hydraulic",
            start_time=30,
            duration=25,
            severity=0.6,
        ),
    ]
    session = SimSession("op", seed=7, faults=faults, params=quiet_params())
    op = make_operator(session, OperatorConfig(loop=True, jitter_m=0.0, dwell_s=(0.3, 0.3)))
    trace = []
    while session.sim_time < duration - 1e-9:
        session.submit(op.act(session.ground_truth()))
        step = session.step()
        if session.sim.tick % 10 == 0:
            s = _state(session, step.decision)
            s["op_state"] = op.state
            trace.append(s)
    return {"faults": [f.model_dump(mode="json") for f in faults], "trace": trace}


# (keys held, seconds) — drive toward the worker (proximity / worker_block), back off, drive
# into the forbidden zone, work the front to its limits, e-stop, reset, command timeout.
KEY_PLAN = [
    ("d", 1.6),
    ("w", 8.0),
    ("ey", 2.0),
    ("s", 2.0),
    ("a", 1.2),
    ("w", 10.0),
    ("", 1.0),
    ("rt", 2.0),
    ("fg", 1.5),
    ("h", 1.0),
    ("q", 1.5),
    ("we", 1.0),
    ("TIMEOUT", 1.0),
    ("r", 0.5),
    (" ", 0.5),
    ("", 1.0),
    ("RESET", 0.0),
    ("s", 3.0),
]


def sim_keyboard_trace() -> dict:
    session = SimSession("kb", seed=3, params=quiet_params())
    seq = 0
    trace = []
    for keys, dur in KEY_PLAN:
        if keys == "RESET":
            session.safety.reset_estop()
            continue
        n = round(dur / session.dt)
        for _ in range(n):
            if keys != "TIMEOUT":
                seq += 1
                session.submit(keyboard_to_command(list(keys), seq, session.sim_time))
            step = session.step()
            if session.sim.tick % 5 == 0:
                trace.append(_state(session, step.decision))
    return {"plan": KEY_PLAN, "trace": trace}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    bundle = DetectorBundle.load(a.model)
    with open(CASES, encoding="utf-8") as f:
        cases = yaml.safe_load(f)["cases"]
    doc = {
        "detectors": [detector_case(bundle, c) for c in cases],
        "sim_operator": sim_operator_trace(),
        "sim_keyboard": sim_keyboard_trace(),
    }
    Path(a.out).write_text(json.dumps(doc), encoding="utf-8")
    n = sum(len(c["windows"]) for c in doc["detectors"])
    print(
        f"wrote {a.out}: {n} windows, {len(doc['sim_operator']['trace'])} + "
        f"{len(doc['sim_keyboard']['trace'])} sim states"
    )


if __name__ == "__main__":
    main()
