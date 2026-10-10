import type {
  CompareResponse,
  ControlCommand,
  Report,
  RunInfo,
  SceneConfig,
  SeriesResponse,
  TimelineEvent,
  TwinState,
} from "../types/api";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${path}: ${body.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  req<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  config: () => req<SceneConfig>("/api/config"),
  state: () => req<TwinState>("/api/twin/state"),
  startLive: (seed: number, autopilot: boolean) =>
    post<{ run_id: string }>("/api/runs", { mode: "live", seed, autopilot }),
  startReplay: (runId: string) =>
    post<{ run_id: string }>("/api/runs", { mode: "replay", run_id: runId }),
  stop: () => post<{ report: Report | null }>("/api/runs/stop"),
  runs: () => req<RunInfo[]>("/api/runs"),
  report: (runId: string) => req<Report>(`/api/runs/${encodeURIComponent(runId)}/report`),
  series: (runId: string, keys: string[], tFrom = 0, tTo = 1e9, maxPoints = 1500) =>
    req<SeriesResponse>(
      `/api/runs/${encodeURIComponent(runId)}/series?keys=${encodeURIComponent(keys.join(","))}` +
        `&t_from=${tFrom}&t_to=${tTo}&max_points=${maxPoints}`,
    ),
  control: (cmd: ControlCommand) => post<{ accepted: boolean }>("/api/control", cmd),
  estop: () => post<{ estop: boolean }>("/api/estop"),
  resetEstop: () => post<{ estop: boolean }>("/api/estop/reset"),
  fault: (type: string, target: string, severity: number, duration: number) =>
    post<TimelineEvent>("/api/faults", { type, target, severity, duration }),
  play: () => post("/api/replay/play"),
  pause: () => post("/api/replay/pause"),
  seek: (t: number) => post("/api/replay/seek", { t }),
  speed: (speed: number) => post("/api/replay/speed", { speed }),
  events: (since = 0) => req<TimelineEvent[]>(`/api/twin/events?since=${since}`),
  anomalies: (runId: string) =>
    req<TimelineEvent[]>(`/api/anomalies?run_id=${encodeURIComponent(runId)}`),
  compare: (runId: string, normal: [number, number], abnormal: [number, number]) =>
    req<CompareResponse>(
      `/api/anomalies/compare?run_id=${encodeURIComponent(runId)}` +
        `&normal_from=${normal[0]}&normal_to=${normal[1]}` +
        `&anom_from=${abnormal[0]}&anom_to=${abnormal[1]}`,
    ),
};
