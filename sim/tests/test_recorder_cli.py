from pathlib import Path

from twinpilot_sim.cli import main
from twinpilot_sim.faults import load_fault_scenario
from twinpilot_sim.recorder import read_events, read_meta, read_records, telemetry_hash, to_parquet
from twinpilot_sim.runner import run_scenario

ROOT = Path(__file__).parents[2]
LAG = ROOT / "simulation" / "fault-scenarios" / "joint_lag_bucket.yaml"


def test_same_seed_same_hash(tmp_path):
    faults = load_fault_scenario(LAG)
    a = run_scenario("run-det", 3, 25, tmp_path / "a", faults=faults)
    b = run_scenario("run-det", 3, 25, tmp_path / "b", faults=faults)
    assert telemetry_hash(a.run_dir) == telemetry_hash(b.run_dir)
    c = run_scenario("run-det", 4, 25, tmp_path / "c", faults=faults)
    assert telemetry_hash(a.run_dir) != telemetry_hash(c.run_dir)


def test_recorded_run_roundtrip(tmp_path):
    res = run_scenario("run-rt", 1, 30, tmp_path, faults=load_fault_scenario(LAG))
    meta = read_meta(res.run_dir)
    assert meta.faults[0].type == "JOINT_LAG"
    assert meta.result["delivered"] >= 1
    recs = list(read_records(res.run_dir))
    assert len(recs) == res.records
    events = read_events(res.run_dir)
    assert any(e["kind"] == "fault" for e in events)
    assert any(e.get("type") == "attach" for e in events)
    import pyarrow.parquet as pq

    table = pq.read_table(to_parquet(res.run_dir))
    assert table.num_rows == len(recs)
    assert set(table.column("unit").to_pylist()) >= {"rad", "Nm", "degC"}


def test_cli_run(tmp_path, capsys):
    rc = main(
        ["run", "--seed", "1", "--duration", "10", "--out", str(tmp_path), "--fault", str(LAG)]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "run-joint_lag_bucket-s1" in out
    assert (tmp_path / "run-joint_lag_bucket-s1" / "telemetry.jsonl").exists()
