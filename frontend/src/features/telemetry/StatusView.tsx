import { useMemo } from "react";
import { Chart, axisStyle } from "../../components/Chart";
import { SimBadge } from "../../components/SimBadge";
import { useTwin } from "../../store/useTwin";

const JOINT_LABEL: Record<string, string> = {
  swing_joint: "상부 회전",
  boom_joint: "붐",
  arm_joint: "암",
  bucket_joint: "버킷",
};

const deg = (rad: number) => (rad * 180) / Math.PI;

function Gauge({ label, k, digits = 1 }: { label: string; k: string; digits?: number }) {
  const s = useTwin((st) => st.state.sensor_states?.[k]);
  return (
    <div className="kpi" title={k}>
      <span>
        {label} {s?.stale && <b style={{ color: "var(--warning)", fontSize: 10 }}>STALE</b>}
      </span>
      <b>{s ? s.value.toFixed(digits) : "—"}</b> <span style={{ display: "inline" }}>{s?.unit}</span>
    </div>
  );
}

export function StatusView() {
  const state = useTwin((s) => s.state);
  const samples = useTwin((s) => s.samples);
  const quality = Object.values(state.sensor_states ?? {})[0]?.quality ?? "simulated";
  const joints = state.joint_states ?? {};
  const pose = state.vehicle_pose;
  const sync = state.sync;

  const effortOption = useMemo(
    () => ({
      xAxis: { type: "value" as const, name: "s", ...axisStyle, min: "dataMin" as const },
      yAxis: { type: "value" as const, name: "kN·m", ...axisStyle },
      series: ["boom_joint.effort", "arm_joint.effort"].map((k) => ({
        name: k,
        type: "line" as const,
        showSymbol: false,
        data: samples.map((x) => [x.t, (x.values[k] ?? 0) / 1000]),
      })),
    }),
    [samples],
  );
  const thermalOption = useMemo(
    () => ({
      xAxis: { type: "value" as const, name: "s", ...axisStyle, min: "dataMin" as const },
      yAxis: [
        { type: "value" as const, name: "°C", ...axisStyle, scale: true },
        { type: "value" as const, name: "m/s²", ...axisStyle },
      ],
      series: [
        {
          name: "hydraulic.oil_temperature",
          type: "line" as const,
          showSymbol: false,
          data: samples.map((x) => [x.t, x.values["hydraulic.oil_temperature"] ?? null]),
        },
        {
          name: "chassis.vibration_rms",
          type: "line" as const,
          yAxisIndex: 1,
          showSymbol: false,
          data: samples.map((x) => [x.t, x.values["chassis.vibration_rms"] ?? null]),
        },
      ],
    }),
    [samples],
  );

  if (!state.run_id) return <div className="panel">실행 중인 run이 없다. 조종 탭에서 시작하거나 분석 탭에서 리플레이를 연다.</div>;

  return (
    <div className="grid two">
      <div className="panel">
        <h3>
          관절 상태 <SimBadge quality={quality} />
        </h3>
        <table>
          <thead>
            <tr>
              <th>관절</th>
              <th>각도 (°)</th>
              <th>속도 (rad/s)</th>
              <th>effort (kN·m)</th>
              <th>명령 오차 (rad/s)</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(JOINT_LABEL).map(([j, label]) => {
              const s = joints[j];
              return (
                <tr key={j}>
                  <td>{label}</td>
                  <td className="mono">{s ? deg(s.position).toFixed(1) : "—"}</td>
                  <td className="mono">{s ? s.velocity.toFixed(3) : "—"}</td>
                  <td className="mono">{s ? (s.effort / 1000).toFixed(1) : "—"}</td>
                  <td className="mono">{s ? s.command_error.toFixed(3) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <h3 style={{ marginTop: 16 }}>장비 위치 · IMU</h3>
        <div className="kpis">
          <div className="kpi">
            <span>위치 x / y (m)</span>
            <b>
              {pose ? `${pose.x.toFixed(1)} / ${pose.y.toFixed(1)}` : "—"}
            </b>
          </div>
          <div className="kpi">
            <span>방위 (°)</span>
            <b>{pose ? deg(pose.yaw).toFixed(0) : "—"}</b>
          </div>
          <div className="kpi">
            <span>롤 / 피치 (°)</span>
            <b>{pose ? `${deg(pose.roll).toFixed(1)} / ${deg(pose.pitch).toFixed(1)}` : "—"}</b>
          </div>
          <Gauge label="좌 트랙" k="left_track.velocity" digits={2} />
          <Gauge label="우 트랙" k="right_track.velocity" digits={2} />
        </div>
      </div>

      <div className="panel">
        <h3>
          장비 상태 추정값 <SimBadge quality={quality} />
        </h3>
        <div className="kpis">
          <Gauge label="유압유 온도" k="hydraulic.oil_temperature" />
          <Gauge label="엔진 부하" k="engine.load" />
          <Gauge label="진동 RMS" k="chassis.vibration_rms" digits={2} />
          <Gauge label="누적 에너지" k="energy.consumed" digits={0} />
        </div>
        <p className="note">
          유압 온도·엔진 부하·진동·에너지는 시뮬레이션 유압 일률에서 계산한 가상 값이다. 실제 센서 측정값이 아니다.
        </p>
        <Chart option={thermalOption} />
      </div>

      <div className="panel">
        <h3>Effort 추이</h3>
        <Chart option={effortOption} />
      </div>

      <div className="panel">
        <h3>시간 동기화</h3>
        {sync ? (
          <table>
            <tbody>
              <tr>
                <td>수신 레코드</td>
                <td className="mono">{sync.received.toLocaleString()}</td>
              </tr>
              <tr>
                <td>누락 / 비율</td>
                <td className="mono">
                  {sync.missing} / {(sync.missing_ratio * 100).toFixed(3)}%
                </td>
              </tr>
              <tr>
                <td>순서 역전 비율</td>
                <td className="mono">{(sync.out_of_order_ratio * 100).toFixed(3)}%</td>
              </tr>
              <tr>
                <td>생성 → 백엔드 지연 (평균 / 최대)</td>
                <td className="mono">
                  {state.mode === "live"
                    ? `${sync.latency_ms_mean.toFixed(2)} / ${sync.latency_ms_max.toFixed(2)} ms`
                    : "리플레이: 해당 없음"}
                </td>
              </tr>
              <tr>
                <td>오래된 센서</td>
                <td className="mono">{state.stale_sensors?.length ? state.stale_sensors.join(", ") : "없음"}</td>
              </tr>
            </tbody>
          </table>
        ) : (
          <p className="note">데이터 없음</p>
        )}
        <p className="note">센서 데이터는 도착 순서가 아니라 simulation time 기준으로 정렬해 twin state를 만든다.</p>
      </div>
    </div>
  );
}
