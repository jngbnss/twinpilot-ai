import numpy as np

from twinpilot_ai.features.dataset import window_run
from twinpilot_ai.training.make_dataset import RunSpec, load_manifest, make_dataset, plan
from twinpilot_sim.schema import FaultSpec


def test_plan_splits_are_disjoint_and_train_is_normal_only():
    specs = plan()
    ids = [s.run_id for s in specs]
    assert len(ids) == len(set(ids))
    by_split = {}
    for s in specs:
        by_split.setdefault(s.split, set()).add(s.run_id)
    names = list(by_split)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            assert not by_split[a] & by_split[b]
    assert all(not s.faults for s in specs if s.split == "train")
    assert {s.split for s in specs} == {"train", "val", "test", "unseen"}
    seeds = [s.seed for s in specs]
    assert len(seeds) == len(set(seeds))
    assert plan()[50].faults == specs[50].faults  # deterministic


def test_small_dataset_and_labels(tmp_path):
    fault = FaultSpec(
        fault_id="f1",
        type="JOINT_LAG",
        target="bucket_joint",
        start_time=10,
        duration=8,
        severity=0.9,
    )
    specs = [
        RunSpec("n0", "train", 1, duration=24),
        RunSpec("f0", "test", 2, [fault], duration=24),
    ]
    path = make_dataset("vt", tmp_path, workers=1, specs=specs)
    m = load_manifest(path)
    assert m["counts"] == {"test": 1, "train": 1}
    assert all(len(r["parquet_sha256"]) == 64 for r in m["runs"])
    rw = window_run(tmp_path / "vt" / "runs" / "f0", m["runs"][1]["faults"])
    assert rw.X.shape[0] == len(rw.t_end) == len(rw.y)
    assert np.isfinite(rw.X).all()
    pos = rw.t_end[rw.y == 1]
    assert pos.min() >= 11.0 - 1e-9 and pos.max() <= 19.0 + 1e-9  # >= 50% overlap
    assert (rw.fault_type[rw.y == 1] == "JOINT_LAG").all()
    assert rw.ignore[(rw.t_end > 18.0) & (rw.t_end < 21)].any()  # post-fault grace
    # deterministic features
    rw2 = window_run(tmp_path / "vt" / "runs" / "f0", m["runs"][1]["faults"])
    assert np.array_equal(rw.X, rw2.X)
