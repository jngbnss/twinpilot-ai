import { useState } from "react";
import { api } from "../../services/api";
import { useTwin } from "../../store/useTwin";

const TARGETS: Record<string, string[]> = {
  JOINT_LAG: ["bucket_joint", "arm_joint", "boom_joint", "swing_joint"],
  FRICTION_INCREASE: ["boom_joint", "arm_joint", "bucket_joint", "swing_joint"],
  SENSOR_NOISE: ["arm_joint.effort", "boom_joint.velocity", "imu.roll", "chassis.vibration_rms"],
  SENSOR_STUCK: ["boom_joint.position", "swing_joint.position", "imu.pitch", "bucket_joint.velocity"],
  TEMP_RISE: ["hydraulic"],
};

/** Development panel: inject a labeled fault into the running live simulation. */
export function FaultPanel() {
  const live = useTwin((s) => s.state.mode === "live" && s.state.active);
  const [type, setType] = useState("JOINT_LAG");
  const [target, setTarget] = useState(TARGETS.JOINT_LAG[0]);
  const [severity, setSeverity] = useState(0.8);
  const [duration, setDuration] = useState(20);
  const [last, setLast] = useState<string | null>(null);
  const inject = async () => {
    try {
      const ev = await api.fault(type, target, severity, duration);
      setLast(`${ev.type} → ${ev.target} @ ${Number(ev.start_time).toFixed(1)} s`);
    } catch (e) {
      setLast(String(e));
    }
  };
  return (
    <div className="panel">
      <h3>
        Fault Injection <span className="badge-sim">DEV</span>
      </h3>
      <div className="row">
        <select
          value={type}
          onChange={(e) => {
            setType(e.target.value);
            setTarget(TARGETS[e.target.value][0]);
          }}
        >
          {Object.keys(TARGETS).map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          {TARGETS[type].map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <label className="muted">
          severity {severity.toFixed(1)}{" "}
          <input
            type="range"
            min={0.1}
            max={1}
            step={0.1}
            value={severity}
            onChange={(e) => setSeverity(Number(e.target.value))}
          />
        </label>
        <label className="muted">
          지속{" "}
          <input
            type="number"
            min={2}
            max={120}
            value={duration}
            style={{ width: 60 }}
            onChange={(e) => setDuration(Number(e.target.value))}
          />{" "}
          s
        </label>
        <button disabled={!live} onClick={inject}>
          주입
        </button>
      </div>
      {last && <p className="note">마지막 주입: {last}</p>}
      <p className="note">주입한 고장은 평가용 정답 라벨로 run 기록에 저장된다.</p>
    </div>
  );
}
