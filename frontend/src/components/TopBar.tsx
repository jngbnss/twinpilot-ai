import { useTwin } from "../store/useTwin";

export function TopBar() {
  const state = useTwin((s) => s.state);
  const status = useTwin((s) => s.status);
  const latency = useTwin((s) => s.latencyMs);
  const mode = state.mode === "idle" ? "IDLE" : state.mode.toUpperCase();
  return (
    <header className="topbar">
      <div className="brand">
        TwinPilot AI
        <small>Simulation-based Digital Twin Prototype · Level 1</small>
      </div>
      <span className="pill" title="WebSocket">
        <span className={`dot ${status}`} />
        {status === "open" ? "연결됨" : status === "connecting" ? "연결 중" : "끊김"}
        {latency !== null && <b>{latency.toFixed(0)} ms</b>}
      </span>
      <span className="pill">
        모드 <b>{mode}</b>
        {state.autopilot && <b>AUTOPILOT</b>}
      </span>
      <span className="pill">
        run <b>{state.run_id ?? "—"}</b>
      </span>
      <span className="pill">
        sim time <b>{(state.simulation_time ?? 0).toFixed(1)} s</b>
      </span>
      <span className="spacer" />
      <span className="badge-sim">SIMULATED SENSOR</span>
    </header>
  );
}
