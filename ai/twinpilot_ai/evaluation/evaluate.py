"""Run experiments 1-3 on a dataset manifest and write a Markdown + JSON report.

uv run python -m twinpilot_ai.evaluation.evaluate --manifest datasets/manifests/v1.yaml
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from twinpilot_ai.anomaly.detectors import ThresholdDetector, ZScoreDetector
from twinpilot_ai.evaluation.metrics import evaluate_detector
from twinpilot_ai.features.windows import FEATURE_SETS
from twinpilot_ai.training.make_dataset import load_manifest
from twinpilot_ai.training.train import DEFAULT_MODEL, load_splits, stack, train_bundle

LABELS = {
    "threshold": "고정 임계값 (p99.5 한계, val 보정)",
    "zscore": "Z-score (val 보정)",
    "zscore-tail": "Tail-normalized Z-score",
    "isolation-forest": "Isolation Forest",
}


def _row(name: str, r: dict[str, Any]) -> str:
    o = r["overall"]
    return (
        f"| {name} | {o['precision']:.3f} | {o['recall']:.3f} | {o['f1']:.3f} | {o['fpr']:.3f} "
        f"| {r['run_detection_rate']:.2f} | {r['median_delay_s']} | {r['false_alarms_per_hour']} |"
    )


HEADER = (
    "| 모델 | Precision | Recall | F1 | FPR | run 탐지율 | 탐지 지연 중앙값(s) | 오경보/h |\n"
    "|------|-----------|--------|----|-----|-----------|--------------------|---------|"
)


def run_experiments(manifest: str, out_dir: Path, model_out: Path | None) -> dict[str, Any]:
    t0 = time.time()
    m = load_manifest(manifest)
    version = m["dataset_version"]
    results: dict[str, Any] = {"dataset_version": version, "manifest": manifest}

    data: dict[str, dict[str, list]] = {fs: {} for fs in FEATURE_SETS}
    for split in ("train", "val", "test", "unseen"):
        per_fs = load_splits(manifest, (split,), FEATURE_SETS)
        for fs in FEATURE_SETS:
            data[fs][split] = per_fs[fs]

    # Experiment 1: baselines vs Isolation Forest (full features)
    full = data["full"]
    bundle = train_bundle(full["train"], full["val"], version, "full")
    exp1 = {k: evaluate_detector(d, full["test"]) for k, d in bundle.detectors.items()}
    # rule baselines with their textbook (uncalibrated) thresholds for reference
    X_tr = stack(full["train"], normal_only=True)
    names = full["train"][0].names
    raw_th = ThresholdDetector().fit(X_tr, names)
    raw_z = ZScoreDetector(4.0).fit(X_tr, names)
    exp1["threshold-uncalibrated"] = evaluate_detector(raw_th, full["test"])
    exp1["zscore-uncalibrated(|z|>4)"] = evaluate_detector(raw_z, full["test"])
    results["experiment_1"] = exp1
    # Experiment 2: feature sets x {Z-score, Isolation Forest}; selection on VAL only
    exp2: dict[str, Any] = {}
    selection: list[tuple[float, str, str]] = []
    bundles = {"full": bundle}
    for fs in FEATURE_SETS:
        b = bundles.get(fs) or train_bundle(data[fs]["train"], data[fs]["val"], version, fs)
        bundles[fs] = b
        for k in ("zscore", "zscore-tail", "isolation-forest"):
            r = evaluate_detector(b.detectors[k], data[fs]["test"])
            r["n_features"] = len(b.feature_names)
            val_f1 = evaluate_detector(b.detectors[k], data[fs]["val"])["overall"]["f1"]
            r["val_f1"] = val_f1
            exp2[f"{k}|{fs}"] = r
            selection.append((val_f1, k, fs))
    results["experiment_2"] = exp2
    best_f1, best_det, best_fs = max(selection)
    selected = bundles[best_fs]
    selected.primary = best_det
    selected.calibration["selected_by"] = f"val F1 = {best_f1}"
    results["selected"] = {"detector": best_det, "feature_set": best_fs, "val_f1": best_f1}
    if model_out is not None:
        selected.save(model_out)
        results["model_path"] = str(model_out)
    results["calibration"] = selected.calibration

    # Experiment 3: unseen conditions with the selected bundle
    unseen = data[best_fs]["unseen"]
    conds = sorted({r.run_id.split("-")[1] for r in unseen})
    exp3: dict[str, Any] = {}
    for k, d in selected.detectors.items():
        exp3[k] = {"all": evaluate_detector(d, unseen)}
        for c in conds:
            subset = [r for r in unseen if r.run_id.split("-")[1] == c]
            exp3[k][c] = evaluate_detector(d, subset)
    results["experiment_3"] = exp3
    results["experiment_3_feature_set"] = best_fs
    results["selected_test"] = {
        k: evaluate_detector(d, data[best_fs]["test"]) for k, d in selected.detectors.items()
    }
    results["elapsed_s"] = round(time.time() - t0, 1)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"eval-{version}.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (out_dir / f"eval-{version}.md").write_text(render(results, m), encoding="utf-8")
    return results


def render(res: dict[str, Any], manifest: dict[str, Any]) -> str:
    v = res["dataset_version"]
    lines = [
        f"# 이상 탐지 평가 리포트 — dataset {v}",
        "",
        "> 시뮬레이션 데이터(Level 1)와 주입된 고장 라벨로 평가한 결과."
        " 실제 장비 고장 진단 성능이 아니다.",
        "",
        f"- 데이터: {manifest['counts']} runs, run 단위 분할, train = 정상만",
        f"- 코드 버전: `{manifest['code_version']}`, params `{manifest['params_hash']}`",
        "- 창: 2 s / stride 0.5 s, 라벨 = 창의 50% 이상이 고장 구간, 고장 종료 후 10 s 평가 제외",
        "- 임계값: val 정상 창 FPR 1% 보정 (uncalibrated 행은 교과서 임계값)",
        "- 오경보/h: 연속 2창 경보 기준, 고장 구간 밖",
        f"- 소요: {res['elapsed_s']} s",
        "",
        "## 실험 1 — 기준선 vs Isolation Forest (test split, full 특징)",
        "",
        HEADER,
    ]
    for k, r in res["experiment_1"].items():
        lines.append(_row(LABELS.get(k, k), r))
    lines += ["", "### 고장 유형별 (window recall / run 탐지율 / 지연 중앙값 s)", ""]
    types = sorted({t for r in res["experiment_1"].values() for t in r["by_fault_type"]})
    models = [k for k in res["experiment_1"] if "uncalibrated" not in k]
    lines.append("| 유형 | " + " | ".join(LABELS.get(k, k) for k in models) + " |")
    lines.append("|------|" + "|".join("---" for _ in models) + "|")
    for t in types:
        cells = []
        for k in models:
            bt = res["experiment_1"][k]["by_fault_type"].get(t)
            cells.append(
                f"{bt['window_recall']:.2f} / {bt['run_detection_rate']:.2f}"
                f" / {bt['median_delay_s']}"
                if bt
                else "-"
            )
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    lines += ["", "### 심각도별 window recall", ""]
    lines.append("| severity | " + " | ".join(LABELS.get(k, k) for k in models) + " |")
    lines.append("|---|" + "|".join("---" for _ in models) + "|")
    sevs = sorted({s for k in models for s in res["experiment_1"][k]["by_severity"]})
    for s in sevs:
        cells = [
            f"{res['experiment_1'][k]['by_severity'].get(s, {}).get('window_recall', 0):.2f}"
            for k in models
        ]
        lines.append(f"| {s} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 실험 2 — 특징 세트 비교 (test split, 선택은 val F1)",
        "",
        HEADER.replace("| 모델 |", "| 모델 · 특징 세트 (개수) · val F1 |"),
    ]
    for key, r in res["experiment_2"].items():
        k, fs = key.split("|")
        lines.append(_row(f"{LABELS.get(k, k)} · {fs} ({r['n_features']}) · {r['val_f1']:.3f}", r))
    sel = res["selected"]
    lines += [
        "",
        f"**선정 모델 (val 기준):** {LABELS.get(sel['detector'], sel['detector'])} + "
        f"`{sel['feature_set']}` 특징 (val F1 {sel['val_f1']:.3f})",
        "",
        "### 선정 특징 세트에서 3종 비교 (test split)",
        "",
        HEADER,
    ]
    for k, r in res["selected_test"].items():
        lines.append(_row(LABELS.get(k, k), r))
    lines += ["", "| 유형 | " + " | ".join(LABELS.get(k, k) for k in res["selected_test"]) + " |"]
    lines.append("|------|" + "|".join("---" for _ in res["selected_test"]) + "|")
    sel_types = sorted({t for r in res["selected_test"].values() for t in r["by_fault_type"]})
    for t in sel_types:
        cells = []
        for r in res["selected_test"].values():
            bt = r["by_fault_type"].get(t)
            cells.append(
                f"{bt['window_recall']:.2f} / {bt['run_detection_rate']:.2f}"
                f" / {bt['median_delay_s']}"
                if bt
                else "-"
            )
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    lines += [
        "",
        f"## 실험 3 — 미확인 조건 (unseen split, `{res['experiment_3_feature_set']}` 특징)",
        "",
        "| 모델 | 조건 | Precision | Recall | F1 | FPR | run 탐지율 | 오경보/h |",
        "|------|------|-----------|--------|----|-----|-----------|---------|",
    ]
    for k, byc in res["experiment_3"].items():
        for c, r in byc.items():
            o = r["overall"]
            rate = r["run_detection_rate"]
            lines.append(
                f"| {LABELS.get(k, k)} | {c} | {o['precision']:.3f} | {o['recall']:.3f} "
                f"| {o['f1']:.3f} | {o['fpr']:.3f} | {rate if rate is not None else '-'} "
                f"| {r['false_alarms_per_hour']} |"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="datasets/manifests/v1.yaml")
    ap.add_argument("--out", default="docs/eval")
    ap.add_argument("--model-out", default=str(DEFAULT_MODEL))
    a = ap.parse_args()
    res = run_experiments(a.manifest, Path(a.out), Path(a.model_out) if a.model_out else None)
    for k, r in res["experiment_1"].items():
        o = r["overall"]
        print(f"{k:32s} F1={o['f1']:.3f} FPR={o['fpr']:.3f} delay={r['median_delay_s']}")
    print(f"report: {a.out}/eval-{res['dataset_version']}.md ({res['elapsed_s']} s)")


if __name__ == "__main__":
    main()
