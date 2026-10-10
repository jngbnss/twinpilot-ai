"""twinpilot-sim command line."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from twinpilot_sim.faults import load_fault_scenario
from twinpilot_sim.operator import OperatorConfig
from twinpilot_sim.recorder import telemetry_hash
from twinpilot_sim.runner import run_scenario


def _cmd_run(a: argparse.Namespace) -> int:
    faults = [f for path in a.fault for f in load_fault_scenario(path)]
    scenario = a.scenario
    if scenario == "normal" and faults:
        scenario = "+".join(Path(p).stem for p in a.fault)
    run_id = a.run_id or f"run-{scenario}-s{a.seed}"
    res = run_scenario(
        run_id=run_id,
        seed=a.seed,
        duration=a.duration,
        out_dir=a.out,
        faults=faults,
        scenario=scenario,
        operator=OperatorConfig(speed_scale=a.speed_scale, loop=a.loop),
        payload_scale=a.payload_scale,
        until_done=a.until_done,
        parquet=a.parquet,
    )
    m = res.metrics
    print(f"run_id      : {run_id}")
    print(f"output      : {res.run_dir}")
    print(f"records     : {res.records}")
    print(f"sim time    : {m['sim_time']} s")
    print(f"delivered   : {m['delivered']}/{m['target_count']} (completed={m['completed']})")
    print(f"completion  : {m['completion_time']} s")
    print(
        f"safety      : events={m['safety_events']} collisions={m['collisions']} "
        f"worker={m['worker_collisions']} forbidden={m['forbidden_entries']} "
        f"tipover={m['tipover_risks']} drops={m['drops']}"
    )
    print(f"faults      : {[f.fault_id + ':' + f.type for f in faults] or 'none'}")
    print(f"sha256      : {telemetry_hash(res.run_dir)}")
    if a.json:
        print(json.dumps(m))
    return 0


def _cmd_hash(a: argparse.Namespace) -> int:
    print(telemetry_hash(a.run_dir))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="twinpilot-sim", description="TwinPilot excavator simulator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the scripted carry scenario and record it")
    r.add_argument("--scenario", default="normal")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--duration", type=float, default=120.0)
    r.add_argument("--fault", action="append", default=[], help="fault scenario YAML (repeatable)")
    r.add_argument("--out", default="runs")
    r.add_argument("--run-id")
    r.add_argument("--speed-scale", type=float, default=1.0)
    r.add_argument("--payload-scale", type=float, default=1.0)
    r.add_argument("--loop", action="store_true", help="keep carrying objects back and forth")
    r.add_argument("--until-done", action="store_true", help="stop when the task completes")
    r.add_argument("--parquet", action="store_true")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=_cmd_run)
    h = sub.add_parser("hash", help="sha256 of a run's telemetry")
    h.add_argument("run_dir")
    h.set_defaults(func=_cmd_hash)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
