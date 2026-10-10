from pathlib import Path

import pytest

from twinpilot_sim.faults import load_fault_scenario
from twinpilot_sim.runner import run_scenario

ROOT = Path(__file__).parents[2]
LAG = ROOT / "simulation" / "fault-scenarios" / "joint_lag_bucket.yaml"


@pytest.fixture(scope="session")
def recorded_run(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("runs")
    res = run_scenario("run-fixture", 1, 40, out, faults=load_fault_scenario(LAG))
    return res.run_dir
