import type { Report } from "../../types/api";

const METRIC_LABEL: Record<string, string> = {
  sim_time: "실행 시간 (s)",
  delivered: "운반 완료",
  completion_time: "작업 완료 시간 (s)",
  within_time_limit: "제한 시간 내 완료",
  collisions: "충돌",
  worker_collisions: "작업자 충돌",
  worker_proximity_entries: "작업자 위험거리 진입",
  forbidden_entries: "금지 구역 진입",
  drops: "운반물 낙하",
  tipover_risks: "전복 위험",
  safety_events: "안전 이벤트",
  direction_reversals: "불필요 조작 (방향 반전)",
  mean_command_change: "평균 명령 변화량",
  energy_kj: "에너지 사용 추정 (kJ)",
};

export const metricLabel = (k: string) => METRIC_LABEL[k] ?? k;

export function reportMarkdown(r: Report): string {
  const lines = [
    `# TwinPilot 작업 보고서 — ${r.run_id}`,
    "",
    `> ${r.note}`,
    "",
    `## 점수: ${r.score.total} / 100`,
    "",
    `- 작업 완료 ${r.score.completion} / 40, 시간 ${r.score.time} / 20, 안전 ${r.score.safety} / 40`,
    ...Object.entries(r.score.deductions).map(([k, v]) => `- 감점 ${metricLabel(k)}: -${v}`),
    "",
    "## 원시 지표",
    "",
    "| 지표 | 값 |",
    "|---|---|",
    ...Object.entries(r.raw_metrics).map(([k, v]) => `| ${metricLabel(k)} | ${v} |`),
    "",
    "## 고장 주입 (정답 라벨)",
    "",
    ...(r.faults.length
      ? r.faults.map((f) => `- ${f.start_time} s · ${f.type} · ${f.target} · severity ${f.severity}`)
      : ["- 없음"]),
    "",
    "## 이상 탐지 경보",
    "",
    ...(r.anomaly_events.length
      ? r.anomaly_events.map((e) => `- ${e.time} s · ${e.message ?? ""}`)
      : ["- 없음"]),
    r.fault_to_detection_s !== null ? `\n고장 → 첫 경보: ${r.fault_to_detection_s} s` : "",
    "",
    "## 안전 이벤트",
    "",
    ...(r.safety_events.length
      ? r.safety_events.map((e) => `- ${e.time} s · ${e.level} · ${(e.rules ?? []).join(", ")}`)
      : ["- 없음"]),
    "",
  ];
  return lines.join("\n");
}

export function download(name: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
