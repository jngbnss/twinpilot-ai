import { TwinSocket } from "./ws";
import { useTwin } from "../store/useTwin";

describe("WS message -> store", () => {
  beforeEach(() => useTwin.setState({ state: { run_id: null, mode: "idle" }, samples: [] }));

  it("updates twin state and sample buffer", () => {
    const sock = new TwinSocket("ws://x", {
      onState: useTwin.getState().ingest,
      onStatus: () => undefined,
    });
    sock.handle(
      JSON.stringify({
        type: "twin_state",
        data: {
          run_id: "r1",
          mode: "live",
          simulation_time: 1.5,
          sensor_states: {
            "boom_joint.effort": { value: 1200, unit: "Nm", stamp: 1.5, stale: false, quality: "simulated" },
          },
          anomaly_state: { status: "active", normalized_score: 0.4, alarm: false, detectors: {} },
        },
      }),
    );
    const s = useTwin.getState();
    expect(s.state.run_id).toBe("r1");
    expect(s.samples).toHaveLength(1);
    expect(s.samples[0].score).toBe(0.4);
    expect(s.samples[0].values["boom_joint.effort"]).toBe(1200);
  });

  it("resets buffer on new run or backwards replay seek", () => {
    const ingest = useTwin.getState().ingest;
    ingest({ run_id: "r1", mode: "replay", simulation_time: 10 });
    ingest({ run_id: "r1", mode: "replay", simulation_time: 10.1 });
    expect(useTwin.getState().samples).toHaveLength(2);
    ingest({ run_id: "r1", mode: "replay", simulation_time: 2 });
    expect(useTwin.getState().samples).toHaveLength(1);
    ingest({ run_id: "r2", mode: "live", simulation_time: 2.1 });
    expect(useTwin.getState().samples.map((x) => x.t)).toEqual([2.1]);
  });

  it("reports latency from pong", () => {
    let latency = -1;
    const sock = new TwinSocket("ws://x", {
      onState: () => undefined,
      onStatus: () => undefined,
      onLatency: (ms) => (latency = ms),
    });
    sock.handle(JSON.stringify({ type: "pong", t: performance.now() - 5 }));
    expect(latency).toBeGreaterThanOrEqual(5);
  });
});
