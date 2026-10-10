import { create } from "zustand";
import type { SceneConfig, TwinState } from "../types/api";
import type { SocketStatus } from "../services/ws";

export const TRACKED_KEYS = [
  "boom_joint.effort",
  "bucket_joint.command_error",
  "arm_joint.effort",
  "hydraulic.oil_temperature",
  "chassis.vibration_rms",
  "imu.roll",
  "imu.pitch",
] as const;

export interface Sample {
  t: number;
  score: number | null; // normalized anomaly score (1.0 = threshold)
  alarm: boolean;
  rules: Record<string, number>; // detector -> score / threshold
  values: Record<string, number>;
}

const MAX_SAMPLES = 1500; // ~150 s at 10 Hz

interface TwinStore {
  state: TwinState;
  config: SceneConfig | null;
  status: SocketStatus;
  latencyMs: number | null;
  samples: Sample[];
  setConfig: (c: SceneConfig) => void;
  setStatus: (s: SocketStatus) => void;
  setLatency: (ms: number) => void;
  ingest: (s: TwinState) => void;
}

export const useTwin = create<TwinStore>((set) => ({
  state: { run_id: null, mode: "idle" },
  config: null,
  status: "closed",
  latencyMs: null,
  samples: [],
  setConfig: (config) => set({ config }),
  setStatus: (status) => set({ status }),
  setLatency: (latencyMs) => set({ latencyMs }),
  ingest: (s) =>
    set((prev) => {
      const t = s.simulation_time ?? 0;
      let samples = prev.samples;
      const last = samples[samples.length - 1];
      // new run, or replay jumped backwards: restart the buffer
      if (s.run_id !== prev.state.run_id || (last && t < last.t - 0.25)) samples = [];
      if (s.run_id && (!last || t > last.t || samples.length === 0)) {
        const a = s.anomaly_state;
        const rules: Record<string, number> = {};
        for (const [k, d] of Object.entries(a?.detectors ?? {}))
          rules[k] = d.threshold ? d.score / d.threshold : 0;
        const values: Record<string, number> = {};
        for (const k of TRACKED_KEYS) {
          const v = s.sensor_states?.[k]?.value;
          if (v !== undefined) values[k] = v;
        }
        samples = [
          ...samples.slice(-(MAX_SAMPLES - 1)),
          {
            t,
            score: a?.status === "active" ? (a.normalized_score ?? null) : null,
            alarm: Boolean(a?.alarm),
            rules,
            values,
          },
        ];
      }
      return { state: s, samples };
    }),
}));
