from pathlib import Path

import pytest
import yaml

from twinpilot_ai.evaluation.metrics import evaluate_detector
from twinpilot_ai.features.dataset import window_run
from twinpilot_ai.training.fixtures import build_fixture_bundle
from twinpilot_sim.runner import run_scenario
from twinpilot_sim.schema import FaultSpec

CASES = yaml.safe_load(
    (Path(__file__).parents[2] / "evals" / "anomaly-cases.yaml").read_text(encoding="utf-8")
)["cases"]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return build_fixture_bundle(tmp_path_factory.mktemp("fx"))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_anomaly_case(case, bundle, tmp_path):
    faults = [FaultSpec(**case["fault"])] if case["fault"] else []
    res = run_scenario(case["id"], case["seed"], case["duration"], tmp_path, faults, parquet=True)
    rw = window_run(res.run_dir, [f.model_dump(mode="json") for f in faults], bundle.feature_set)
    det = bundle.detectors[bundle.primary]
    r = evaluate_detector(det, [rw])
    exp = case["expect"]
    if "detected" in exp:
        d = r["detections"][0]
        assert d["detected"] == exp["detected"], r["overall"]
        assert d["delay_s"] <= exp["max_delay_s"]
    if "max_window_fpr" in exp:
        assert r["overall"]["fpr"] <= exp["max_window_fpr"]
