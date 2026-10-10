import type { Report } from "../../types/api";
import { reportMarkdown } from "./report";

const r: Report = {
  run_id: "run-x",
  level: "L1-simulation",
  note: "All values come from a simulation.",
  raw_metrics: { delivered: 3, collisions: 1, completed: true },
  score: { total: 78.5, completion: 40, time: 8.5, safety: 30, deductions: { collisions: 10 } },
  faults: [{ kind: "fault", time: 20, start_time: 20, type: "JOINT_LAG", target: "bucket_joint", severity: 0.9 }],
  safety_events: [],
  anomaly_events: [{ kind: "anomaly", time: 21.5, message: "bucket_joint.velocity에서 평소와 다른 패턴" }],
  world_events: [],
  fault_to_detection_s: 1.5,
};

it("markdown report keeps raw metrics next to the score", () => {
  const md = reportMarkdown(r);
  expect(md).toContain("## 점수: 78.5 / 100");
  expect(md).toContain("| 운반 완료 | 3 |");
  expect(md).toContain("| 충돌 | 1 |");
  expect(md).toContain("JOINT_LAG · bucket_joint");
  expect(md).toContain("고장 → 첫 경보: 1.5 s");
});
