import { useCallback, useEffect, useMemo, useState } from "react";
import { Chart, axisStyle } from "../../components/Chart";
import { Scene3D } from "../../components/Scene3D";
import { api } from "../../services/api";
import { useTwin } from "../../store/useTwin";
import type { Report, RunInfo, SeriesResponse, TimelineEvent } from "../../types/api";
import { download, metricLabel, reportMarkdown } from "./report";

const SERIES_KEYS = [
  "bucket_joint.command_error",
  "boom_joint.effort",
  "arm_joint.effort",
  "boom_joint.position",
  "hydraulic.oil_temperature",
  "chassis.vibration_rms",
  "imu.roll",
];

function eventMarks(r: Report | null, cursor: number | null) {
  const marks: object[] = [];
  const add = (events: TimelineEvent[], color: string, label: (e: TimelineEvent) => string, at = (e: TimelineEvent) => e.time) =>
    events.forEach((e) => marks.push({ xAxis: at(e), lineStyle: { color, type: "dashed" }, label: { formatter: label(e), color } }));
  if (r) {
    add(r.faults, "#f2a541", (e) => `fault ${e.type}`, (e) => Number(e.start_time ?? e.time));
    add(r.anomaly_events, "#e5484d", () => "alarm");
    add(r.safety_events.slice(0, 20), "#e8c547", (e) => e.level ?? "safety");
  }
  if (cursor !== null) marks.push({ xAxis: cursor, lineStyle: { color: "#4fb3bf", width: 2, type: "solid" }, label: { formatter: "▶", color: "#4fb3bf" } });
  return marks;
}

function ReplayControls() {
  const replay = useTwin((s) => s.state.replay);
  const mode = useTwin((s) => s.state.mode);
  const [drag, setDrag] = useState<number | null>(null);
  if (mode !== "replay" || !replay) return null;
  return (
    <div className="panel">
      <h3>리플레이</h3>
      <div className="row">
        {replay.playing ? (
          <button onClick={() => api.pause()}>일시정지</button>
        ) : (
          <button className="primary" onClick={() => api.play()}>
            재생
          </button>
        )}
        <select value={replay.speed} onChange={(e) => api.speed(Number(e.target.value))}>
          {[0.5, 1, 2, 4, 8].map((s) => (
            <option key={s} value={s}>
              {s}×
            </option>
          ))}
        </select>
        <span className="mono muted">
          {(drag ?? replay.cursor).toFixed(1)} / {replay.duration.toFixed(1)} s
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={replay.duration}
        step={0.1}
        value={drag ?? replay.cursor}
        style={{ width: "100%", marginTop: 10 }}
        onChange={(e) => setDrag(Number(e.target.value))}
        onMouseUp={() => {
          if (drag !== null) api.seek(drag).finally(() => setDrag(null));
        }}
        onKeyUp={() => {
          if (drag !== null) api.seek(drag).finally(() => setDrag(null));
        }}
      />
      <p className="note">기록된 센서 데이터를 재생해 같은 twin state 파이프라인으로 동기화한다 (Level 2 Replay 구조).</p>
    </div>
  );
}

function ReportPanel({ report }: { report: Report }) {
  const raw = report.raw_metrics;
  return (
    <div className="panel">
      <h3>
        성과 보고서
        <span className="spacer" />
        <button onClick={() => download(`${report.run_id}.json`, JSON.stringify(report, null, 2), "application/json")}>
          JSON
        </button>
        <button onClick={() => download(`${report.run_id}.md`, reportMarkdown(report), "text/markdown")}>
          Markdown
        </button>
      </h3>
      <div className="kpis">
        <div className="kpi">
          <span>종합 점수 (참고)</span>
          <b>{report.score.total}</b>
        </div>
        <div className="kpi">
          <span>완료 / 시간 / 안전</span>
          <b style={{ fontSize: 16 }}>
            {report.score.completion} / {report.score.time} / {report.score.safety}
          </b>
        </div>
        <div className="kpi">
          <span>고장 → 첫 경보</span>
          <b>{report.fault_to_detection_s !== null ? `${report.fault_to_detection_s} s` : "—"}</b>
        </div>
      </div>
      <table style={{ marginTop: 12 }}>
        <tbody>
          {Object.entries(raw).map(([k, v]) => (
            <tr key={k}>
              <td>{metricLabel(k)}</td>
              <td className="mono">{typeof v === "boolean" ? (v ? "예" : "아니오") : String(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note">종합 점수는 원시 지표와 함께 본다. {report.note}</p>
      <h3 style={{ marginTop: 12 }}>이벤트</h3>
      {report.faults.map((f, i) => (
        <p key={`f${i}`} className="note">
          <b style={{ color: "var(--accent)" }}>FAULT</b> {Number(f.start_time).toFixed(1)} s · {f.type} · {f.target} · sev{" "}
          {f.severity}
        </p>
      ))}
      {report.anomaly_events.slice(0, 6).map((e, i) => (
        <p key={`a${i}`} className="note">
          <b style={{ color: "var(--danger)" }}>ALARM</b> {e.time.toFixed(1)} s · {e.message}
        </p>
      ))}
      {report.safety_events.slice(0, 6).map((e, i) => (
        <p key={`s${i}`} className="note">
          <b style={{ color: "var(--caution)" }}>{e.level}</b> {e.time.toFixed(1)} s · {(e.rules ?? []).join(", ")}
        </p>
      ))}
    </div>
  );
}

export function AnalysisView() {
  const state = useTwin((s) => s.state);
  const cfg = useTwin((s) => s.config);
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [series, setSeries] = useState<SeriesResponse | null>(null);
  const [keys, setKeys] = useState<string[]>(["bucket_joint.command_error", "boom_joint.effort"]);

  const refresh = useCallback(() => api.runs().then(setRuns).catch(() => undefined), []);
  useEffect(() => {
    refresh();
  }, [refresh, state.active]);

  useEffect(() => {
    if (!selected) return;
    setReport(null);
    api.report(selected).then(setReport).catch(() => setReport(null));
  }, [selected]);

  useEffect(() => {
    if (!selected || keys.length === 0) return;
    api.series(selected, keys).then(setSeries).catch(() => setSeries(null));
  }, [selected, keys]);

  const replaying = state.mode === "replay" && state.run_id === selected;
  const cursor = replaying ? (state.replay?.cursor ?? null) : null;
  const option = useMemo(() => {
    const entries = Object.entries(series?.series ?? {});
    return {
      xAxis: { type: "value" as const, name: "s", ...axisStyle, min: "dataMin" as const },
      // ECharts needs at least one y axis even before the series arrive
      yAxis: (entries.length ? entries.slice(0, 2) : [["", { unit: "" }] as const]).map(([, s], i) => ({
        type: "value" as const,
        name: s.unit,
        position: i === 0 ? ("left" as const) : ("right" as const),
        ...axisStyle,
        splitLine: { show: i === 0, lineStyle: { color: "#1f2a31" } },
        scale: true,
      })),
      series: entries.map(([k, s], i) => ({
        name: k,
        type: "line" as const,
        showSymbol: false,
        yAxisIndex: Math.min(i, 1),
        data: s.t.map((t, j) => [t, s.v[j]]),
        markLine: i === 0 ? { symbol: "none", silent: true, data: eventMarks(report, cursor) } : undefined,
      })),
      grid: { left: 64, right: 80, top: 56, bottom: 48 },
      dataZoom: [{ type: "inside" as const }, { type: "slider" as const, height: 18, bottom: 0 }],
    };
  }, [series, report, cursor]);

  return (
    <div className="grid two">
      <div className="panel">
        <h3>
          실행 기록
          <span className="spacer" />
          <button onClick={refresh}>새로고침</button>
        </h3>
        <div style={{ maxHeight: 320, overflow: "auto" }}>
          <table>
            <thead>
              <tr>
                <th>run</th>
                <th>시나리오</th>
                <th>시간 (s)</th>
                <th>운반</th>
                <th>fault</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr
                  key={r.run_id}
                  data-testid="run-row"
                  onClick={() => setSelected(r.run_id)}
                  style={{ cursor: "pointer", background: r.run_id === selected ? "var(--panel-2)" : undefined }}
                >
                  <td className="mono">{r.run_id}</td>
                  <td>{r.scenario}</td>
                  <td className="mono">{r.duration.toFixed(0)}</td>
                  <td className="mono">{String(r.result.delivered ?? "—")}</td>
                  <td className="mono">{r.faults.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {selected && (
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" onClick={() => api.startReplay(selected)}>
              리플레이 열기
            </button>
            <span className="muted mono">{selected}</span>
          </div>
        )}
      </div>

      <div className="stack">
        <ReplayControls />
        {replaying && cfg && (
          <div className="panel scene" style={{ height: 300 }}>
            <Scene3D state={state} cfg={cfg} view="orbit" />
            <div className="overlay">
              <span className="badge-sim">REPLAY</span>
            </div>
          </div>
        )}
      </div>

      {selected && (
        <div className="panel" style={{ gridColumn: "1 / -1" }}>
          <h3>
            센서 그래프 · 이벤트 마커
            <span className="spacer" />
            <select
              multiple
              value={keys}
              onChange={(e) => setKeys(Array.from(e.target.selectedOptions, (o) => o.value).slice(0, 3))}
              style={{ height: 60 }}
            >
              {SERIES_KEYS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </h3>
          <Chart option={option} className="chart tall" />
          <p className="note">주황 = 고장 주입(정답 라벨), 빨강 = AI 경보, 노랑 = 안전 이벤트, 청록 = 리플레이 위치.</p>
        </div>
      )}

      {report && <ReportPanel report={report} />}
    </div>
  );
}
