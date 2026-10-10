import { useMemo, useState } from "react";
import { Chart, axisStyle } from "../../components/Chart";
import { api } from "../../services/api";
import { useTwin } from "../../store/useTwin";
import type { CompareResponse, TimelineEvent } from "../../types/api";
import { useEvents } from "./useEvents";

const DETECTOR_LABEL: Record<string, string> = {
  threshold: "고정 임계값 (규칙)",
  zscore: "Z-score (규칙)",
  "zscore-tail": "Tail-Z (선정 모델)",
  "isolation-forest": "Isolation Forest",
};

function markLines(events: TimelineEvent[]) {
  return events
    .filter((e) => e.kind === "fault" || e.kind === "anomaly")
    .map((e) => ({
      xAxis: e.kind === "fault" ? (e.start_time ?? e.time) : e.time,
      lineStyle: { color: e.kind === "fault" ? "#f2a541" : "#e5484d", type: "dashed" as const },
      label: { formatter: e.kind === "fault" ? `fault ${e.type ?? ""}` : "alarm", color: "#dfe6ea" },
    }));
}

export function ScoreTimeline({ events }: { events: TimelineEvent[] }) {
  const samples = useTwin((s) => s.samples);
  const option = useMemo(() => {
    const keys = Object.keys(samples[samples.length - 1]?.rules ?? {});
    return {
      xAxis: { type: "value" as const, name: "s", min: "dataMin" as const, ...axisStyle },
      yAxis: { type: "value" as const, ...axisStyle, max: (v: { max: number }) => Math.max(2, Math.ceil(v.max)) },
      series: keys.map((k, i) => ({
        name: DETECTOR_LABEL[k] ?? k,
        type: "line" as const,
        showSymbol: false,
        lineStyle: { width: k === "zscore-tail" ? 2.5 : 1.2 },
        data: samples.map((x) => [x.t, Math.min(x.rules[k] ?? 0, 10)]),
        markLine:
          i === 0
            ? {
                symbol: "none",
                silent: true,
                data: [
                  { yAxis: 1, lineStyle: { color: "#e5484d" }, label: { formatter: "threshold", color: "#e5484d" } },
                  ...markLines(events),
                ],
              }
            : undefined,
      })),
    };
  }, [samples, events]);
  return <Chart option={option} className="chart tall" />;
}

function defaultRanges(events: TimelineEvent[], now: number): [[number, number], [number, number]] {
  const f = [...events].reverse().find((e) => e.kind === "fault");
  if (!f) return [[Math.max(0, now - 30), Math.max(1, now - 15)], [Math.max(1, now - 10), now]];
  const start = Number(f.start_time ?? f.time);
  const end = Math.min(now, start + Number(f.duration ?? 10));
  return [
    [Math.max(0, start - 14), Math.max(0.5, start - 2)],
    [start + 0.5, Math.max(start + 1, end)],
  ];
}

function ComparePanel({ events }: { events: TimelineEvent[] }) {
  const runId = useTwin((s) => s.state.run_id);
  const now = useTwin((s) => s.state.simulation_time ?? 0);
  const [ranges, setRanges] = useState<[[number, number], [number, number]] | null>(null);
  const [res, setRes] = useState<CompareResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const r = ranges ?? defaultRanges(events, now);
  const set = (i: 0 | 1, j: 0 | 1, v: number) => {
    const next: [[number, number], [number, number]] = [[...r[0]], [...r[1]]];
    next[i][j] = v;
    setRanges(next);
  };
  const run = async () => {
    if (!runId) return;
    setErr(null);
    try {
      setRes(await api.compare(runId, r[0], r[1]));
    } catch (e) {
      setErr(String(e));
    }
  };
  const num = (i: 0 | 1, j: 0 | 1) => (
    <input type="number" step={0.5} style={{ width: 72 }} value={r[i][j].toFixed(1)} onChange={(e) => set(i, j, Number(e.target.value))} />
  );
  return (
    <div className="panel" style={{ gridColumn: "1 / -1" }}>
      <h3>정상 구간 vs 이상 구간 비교</h3>
      <div className="row">
        <span className="muted">정상</span> {num(0, 0)} ~ {num(0, 1)} s
        <span className="muted" style={{ marginLeft: 12 }}>
          비교
        </span>{" "}
        {num(1, 0)} ~ {num(1, 1)} s
        <button onClick={() => setRanges(null)}>최근 fault 기준</button>
        <button className="primary" disabled={!runId} onClick={run}>
          비교
        </button>
      </div>
      {err && <p className="alarm-text">{err}</p>}
      {res && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>센서</th>
                <th>평균 (정상 → 비교)</th>
                <th>평균 이동 (σ)</th>
                <th>표준편차 비</th>
                <th>고주파 변동 비</th>
              </tr>
            </thead>
            <tbody>
              {res.sensors.slice(0, 8).map((row) => (
                <tr key={row.sensor}>
                  <td>{row.sensor}</td>
                  <td className="mono">
                    {row.normal.mean.toFixed(3)} → {row.abnormal.mean.toFixed(3)} {row.unit}
                  </td>
                  <td className="mono">{row.mean_shift_sigma.toFixed(2)}</td>
                  <td className="mono">{row.std_ratio.toFixed(2)}</td>
                  <td className="mono" style={{ color: row.hf_ratio > 2 || row.hf_ratio < 0.2 ? "var(--danger)" : undefined }}>
                    {row.hf_ratio.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            {res.note} 고주파 변동 비가 0에 가까우면 값이 고정된 센서일 수 있다. 정상 구간은 비교 구간과 비슷한 작업
            단계(주행/스윙/굴착)로 고르는 것이 좋다.
          </p>
        </div>
      )}
    </div>
  );
}

export function AIView() {
  const a = useTwin((s) => s.state.anomaly_state);
  const runId = useTwin((s) => s.state.run_id);
  const events = useEvents();
  const anomalyEvents = events.filter((e) => e.kind === "anomaly").slice(-5).reverse();

  if (!runId) return <div className="panel">실행 중인 run이 없다.</div>;
  if (!a || a.status === "model_not_loaded")
    return (
      <div className="panel">
        <h3>AI 이상 탐지</h3>
        <p className="alarm-text">모델이 로드되지 않았다.</p>
        <p className="note">
          <code>uv run python -m twinpilot_ai.evaluation.evaluate</code> 로 모델을 만든 뒤 백엔드를 재시작한다. ({a?.model_path})
        </p>
      </div>
    );

  return (
    <div className="grid two">
      <div className="panel">
        <h3>이상 탐지 상태</h3>
        <div className="kpis">
          <div className="kpi">
            <span>상태</span>
            <b className={a.alarm ? "alarm-text" : ""}>{a.status !== "active" ? "준비 중" : a.alarm ? "경보" : a.is_anomaly ? "의심" : "정상"}</b>
          </div>
          <div className="kpi">
            <span>점수 / 임계값</span>
            <b>
              {a.anomaly_score?.toFixed(2) ?? "—"} / {a.threshold?.toFixed(2) ?? "—"}
            </b>
          </div>
          <div className="kpi">
            <span>모델</span>
            <b style={{ fontSize: 13 }}>{a.model_version ?? "—"}</b>
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <span className="muted">관련 센서: </span>
          {(a.affected_sensors ?? []).length === 0 ? (
            <span className="muted">없음</span>
          ) : (
            a.affected_sensors!.map((s) => (
              <span key={s} className="chip on">
                {s}
              </span>
            ))
          )}
        </div>
        {a.message && <p className="alarm-text">{a.message}</p>}
        <p className="note">
          AI는 &ldquo;평소와 다른 패턴&rdquo;만 알린다. 고장 원인을 확정하지 않으며, 안전 계층(비상 정지·제한)에 입력되지 않는다.
        </p>
        <h3 style={{ marginTop: 14 }}>규칙 기반 vs 모델</h3>
        <table>
          <thead>
            <tr>
              <th>탐지기</th>
              <th>점수</th>
              <th>임계값</th>
              <th>판정</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(a.detectors ?? {}).map(([k, d]) => (
              <tr key={k}>
                <td>
                  {DETECTOR_LABEL[k] ?? k}
                  {k === a.primary && " ★"}
                </td>
                <td className="mono">{d.score.toFixed(3)}</td>
                <td className="mono">{d.threshold.toFixed(3)}</td>
                <td>{d.is_anomaly ? <span className="chip on">이상</span> : <span className="chip">정상</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="panel">
        <h3>이상 점수 타임라인 (임계값 = 1)</h3>
        <ScoreTimeline events={events} />
        <h3 style={{ marginTop: 10 }}>최근 경보</h3>
        {anomalyEvents.length === 0 ? (
          <p className="note">경보 없음</p>
        ) : (
          anomalyEvents.map((e, i) => (
            <p key={i} className="note">
              <b className="mono">{e.time.toFixed(1)} s</b> · {e.message}
            </p>
          ))
        )}
      </div>
      <ComparePanel events={events} />
    </div>
  );
}
