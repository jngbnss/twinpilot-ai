import { fireEvent, render, screen } from "@testing-library/react";
import { useTwin } from "../store/useTwin";
import type { TwinState } from "../types/api";

vi.mock("../components/Chart", () => ({ Chart: () => <div data-testid="chart" />, axisStyle: {} }));
const estop = vi.fn(() => Promise.resolve({ estop: true }));
vi.mock("../services/api", () => ({
  api: { estop: () => estop(), resetEstop: vi.fn(), events: () => Promise.resolve([]) },
}));

import { EStopButton } from "./control/ControlView";
import { StatusView } from "./telemetry/StatusView";
import { AIView } from "./anomalies/AIView";

const base: TwinState = {
  run_id: "r1",
  mode: "live",
  active: true,
  simulation_time: 10,
  joint_states: {
    boom_joint: { position: 0.6, velocity: 0.1, effort: 52000, command: 0.1, command_error: 0.01 },
  },
  sensor_states: {
    "hydraulic.oil_temperature": { value: 46.2, unit: "degC", stamp: 10, stale: false, quality: "simulated" },
  },
  vehicle_pose: { x: 0, y: 0, yaw: 1.57, roll: 0, pitch: 0 },
  sync: { received: 10, missing: 0, out_of_order: 0, missing_ratio: 0, out_of_order_ratio: 0, latency_ms_mean: 0.3, latency_ms_max: 1 },
};

describe("dashboard views", () => {
  it("status view shows SIMULATED SENSOR badges and values with units", () => {
    useTwin.setState({ state: base, samples: [] });
    render(<StatusView />);
    expect(screen.getAllByText("SIMULATED SENSOR").length).toBeGreaterThan(0);
    expect(screen.getByText("46.2")).toBeInTheDocument();
    expect(screen.getByText("degC")).toBeInTheDocument();
  });

  it("e-stop button calls the API regardless of AI state", () => {
    useTwin.setState({
      state: { ...base, anomaly_state: { status: "model_not_loaded" } },
    });
    render(<EStopButton />);
    fireEvent.click(screen.getByText(/E-STOP/));
    expect(estop).toHaveBeenCalledTimes(1);
  });

  it("AI view uses non-diagnostic wording and lists affected sensors", () => {
    useTwin.setState({
      state: {
        ...base,
        anomaly_state: {
          status: "active",
          alarm: true,
          is_anomaly: true,
          anomaly_score: 3.2,
          threshold: 1.1,
          affected_sensors: ["bucket_joint.velocity"],
          message: "bucket_joint.velocity에서 평소와 다른 패턴이 감지되었습니다.",
          model_version: "zscore-tail-v1+v1",
          primary: "zscore-tail",
          detectors: { "zscore-tail": { score: 3.2, threshold: 1.1, is_anomaly: true, model_version: "x" } },
        },
      },
      samples: [],
    });
    render(<AIView />);
    expect(screen.getByText("경보")).toBeInTheDocument();
    expect(screen.getAllByText("bucket_joint.velocity").length).toBeGreaterThan(0);
    expect(screen.getByText(/평소와 다른 패턴이 감지/)).toBeInTheDocument();
    expect(screen.queryByText(/고장났습니다/)).toBeNull();
  });
});
