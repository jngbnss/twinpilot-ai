import { useCallback, useMemo, useRef, useState } from "react";
import { Scene3D } from "../../components/Scene3D";
import { api } from "../../services/api";
import { useTwin } from "../../store/useTwin";
import type { ControlCommand } from "../../types/api";
import { FaultPanel } from "../safety/FaultPanel";
import { KEY_BINDINGS } from "./mapping";
import { useControlLoop } from "./useControlLoop";

const RULE_LABELS: Record<string, string> = {
  estop: "비상 정지",
  command_timeout: "명령 타임아웃",
  joint_limit: "관절 한계",
  tipover_risk: "전복 위험",
  tipover_caution: "전복 주의",
  worker_proximity: "작업자 근접",
  worker_block: "작업자 방향 차단",
  forbidden_zone: "금지 구역",
  rate_limited: "변화율 제한",
};

export function SafetyBanner() {
  const safety = useTwin((s) => s.state.safety_state);
  const level = safety?.level ?? "INFO";
  const rules = (safety?.rules ?? []).filter((r) => r !== "rate_limited");
  return (
    <div className={`banner ${level}`} data-testid="safety-banner">
      {level}
      {rules.length > 0 && ` · ${rules.map((r) => RULE_LABELS[r] ?? r).join(", ")}`}
      {safety?.worker_distance !== undefined && (
        <span className="muted" style={{ float: "right", fontWeight: 400 }}>
          작업자 거리 {safety.worker_distance.toFixed(1)} m
        </span>
      )}
    </div>
  );
}

export function EStopButton() {
  const estop = useTwin((s) => s.state.safety_state?.estop);
  const live = useTwin((s) => s.state.mode === "live" && s.state.active);
  return (
    <div className="row" style={{ flexDirection: "column", alignItems: "stretch" }}>
      <button className="danger estop" onClick={() => api.estop().catch(() => undefined)}>
        E-STOP (Space)
      </button>
      {estop && (
        <button disabled={!live} onClick={() => api.resetEstop().catch(() => undefined)}>
          비상 정지 해제
        </button>
      )}
      <span className="note">
        비상 정지는 결정론적 안전 계층에서 처리된다. AI 판단은 이 버튼과 무관하다.
      </span>
    </div>
  );
}

function KeyHud({ keys }: { keys: string[] }) {
  return (
    <div className="keys">
      {Object.entries(KEY_BINDINGS).map(([k, [, , label]]) => (
        <div key={k} className={`key ${keys.includes(k) ? "on" : ""}`}>
          {k.toUpperCase()}
          <small>{label}</small>
        </div>
      ))}
    </div>
  );
}

function RunControls() {
  const state = useTwin((s) => s.state);
  const [seed, setSeed] = useState(1);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };
  const live = state.mode === "live" && state.active;
  return (
    <div className="panel">
      <h3>실행</h3>
      <div className="row">
        <label className="muted">
          seed{" "}
          <input
            type="number"
            value={seed}
            style={{ width: 70 }}
            onChange={(e) => setSeed(Number(e.target.value))}
          />
        </label>
        <button className="primary" disabled={busy} onClick={() => run(() => api.startLive(seed, false))}>
          수동 조종 시작
        </button>
        <button disabled={busy} onClick={() => run(() => api.startLive(seed, true))}>
          자동 조종 시작
        </button>
        <button disabled={busy || !state.active} onClick={() => run(() => api.stop())}>
          종료
        </button>
      </div>
      {live && state.autopilot && (
        <p className="note">자동 조종 중. 키를 누르면 수동 조종으로 전환된다.</p>
      )}
    </div>
  );
}

function TaskPanel() {
  const task = useTwin((s) => s.state.task_state);
  const t = useTwin((s) => s.state.simulation_time ?? 0);
  if (!task) return null;
  return (
    <div className="panel">
      <h3>작업 목표 · 구역 A → B 운반</h3>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span>
          운반 <b className="mono">{task.delivered}</b> / {task.target_count}
        </span>
        <span className="muted mono">
          {t.toFixed(0)} / {task.time_limit_s.toFixed(0)} s
        </span>
      </div>
      <div className="bar" style={{ marginTop: 8 }}>
        <div style={{ width: `${task.progress * 100}%` }} />
      </div>
      <p className="note">버킷 적재: {task.payload ? "있음" : "없음"}</p>
    </div>
  );
}

export function ControlView() {
  const state = useTwin((s) => s.state);
  const cfg = useTwin((s) => s.config);
  const [view, setView] = useState<"orbit" | "cab">("orbit");
  const sendErr = useRef(0);
  const live = state.mode === "live" && Boolean(state.active);
  const sink = useMemo(
    () => ({
      send: (cmd: ControlCommand) => {
        api.control(cmd).catch(() => (sendErr.current += 1));
      },
      estop: () => {
        api.estop().catch(() => undefined);
      },
    }),
    [],
  );
  const { keys, device } = useControlLoop(live, sink);
  const toggle = useCallback(() => setView((v) => (v === "orbit" ? "cab" : "orbit")), []);
  return (
    <div className="grid control">
      <div className="panel scene">
        {cfg ? <Scene3D state={state} cfg={cfg} view={view} /> : <p className="note">설정 로딩 중…</p>}
        <div className="overlay">
          <button onClick={toggle}>{view === "orbit" ? "운전석 시점" : "외부 시점"}</button>
          <span className="badge-sim">SIMULATED</span>
          {device && <span className="pill">입력: {device}</span>}
        </div>
      </div>
      <div className="stack">
        <SafetyBanner />
        <RunControls />
        <div className="panel">
          <h3>조종 입력</h3>
          <KeyHud keys={keys} />
          <p className="note">
            W/S 주행 · A/D 회전 · Q/E 스윙 · R/F 붐 · T/G 암 · Y/H 버킷 · Space 비상 정지. 게임패드: 좌스틱 주행, 우스틱
            붐·암, 트리거 버킷, LB/RB 스윙, B 비상 정지.
          </p>
        </div>
        <EStopButton />
        <TaskPanel />
        <FaultPanel />
      </div>
    </div>
  );
}
