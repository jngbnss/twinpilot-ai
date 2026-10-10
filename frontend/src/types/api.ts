// Types mirroring the backend (backend/app/twin/store.py snapshot, api/routes.py).

export type Mode = "live" | "replay" | "idle";
export type RiskLevel = "INFO" | "CAUTION" | "WARNING" | "EMERGENCY";

export interface JointState {
  position: number;
  velocity: number;
  effort: number;
  command: number;
  command_error: number;
}

export interface SensorState {
  value: number;
  unit: string;
  stamp: number;
  stale: boolean;
  quality: "simulated" | "replayed" | "real";
}

export interface TaskObject {
  object_id: string;
  kind: string;
  x: number;
  y: number;
  z: number;
  state: "resting" | "attached" | "delivered";
  size_m: number;
}

export interface SafetyState {
  level?: RiskLevel;
  rules?: string[];
  estop?: boolean;
  worker_distance?: number;
  command?: Record<string, number>;
}

export interface DetectorResult {
  score: number;
  threshold: number;
  is_anomaly: boolean;
  model_version: string;
}

export interface AnomalyState {
  status?: "active" | "warming_up" | "model_not_loaded";
  timestamp?: number;
  is_anomaly?: boolean;
  alarm?: boolean;
  anomaly_score?: number;
  threshold?: number;
  normalized_score?: number;
  affected_sensors?: string[];
  message?: string;
  model_version?: string;
  primary?: string;
  feature_set?: string;
  detectors?: Record<string, DetectorResult>;
  model_path?: string;
}

export interface SyncStats {
  received: number;
  missing: number;
  out_of_order: number;
  missing_ratio: number;
  out_of_order_ratio: number;
  latency_ms_mean: number;
  latency_ms_max: number;
}

export interface ReplayState {
  cursor: number;
  duration: number;
  speed: number;
  playing: boolean;
}

export interface TwinState {
  run_id: string | null;
  mode: Mode;
  active?: boolean;
  autopilot?: boolean;
  simulation_time?: number;
  vehicle_pose?: { x: number; y: number; yaw: number; roll: number; pitch: number };
  joint_states?: Record<string, JointState>;
  tracks?: Record<string, { velocity: number; command: number }>;
  sensor_states?: Record<string, SensorState>;
  task_state?: {
    delivered: number;
    target_count: number;
    progress: number;
    time_limit_s: number;
    objects: TaskObject[];
    payload: boolean;
  };
  safety_state?: SafetyState;
  anomaly_state?: AnomalyState;
  sync?: SyncStats;
  stale_sensors?: string[];
  replay?: ReplayState;
}

export interface Zone {
  name: string;
  x: number;
  y: number;
  radius: number;
}

export interface RectZone {
  name: string;
  xmin: number;
  xmax: number;
  ymin: number;
  ymax: number;
}

export interface SceneConfig {
  level: string;
  task: {
    name: string;
    time_limit_s: number;
    target_count: number;
    zone_a: Zone;
    zone_b: Zone;
    forbidden_zones: RectZone[];
    worker: { x: number; y: number; radius: number };
    objects: { object_id: string; kind: string; size_m: number }[];
  };
  machine: {
    links: Record<"boom" | "arm" | "bucket", { length_m: number; mass_kg: number }>;
    boom_pivot: { x_m: number; z_m: number };
    body: { footprint_radius_m: number; track_gauge_m: number };
    safety: { worker_min_distance_m: number };
  };
  fault_types: string[];
}

export interface ControlCommand {
  timestamp: number;
  device: "keyboard" | "gamepad" | "script" | "api";
  sequence: number;
  left_track: number;
  right_track: number;
  swing: number;
  boom: number;
  arm: number;
  bucket: number;
  estop: boolean;
}

export interface TimelineEvent {
  kind: "fault" | "world" | "safety" | "anomaly" | "anomaly_cleared" | string;
  time: number;
  type?: string;
  level?: string;
  rules?: string[];
  target?: string;
  severity?: number;
  start_time?: number;
  duration?: number;
  fault_id?: string;
  message?: string;
  affected_sensors?: string[];
  [k: string]: unknown;
}

export interface RunInfo {
  run_id: string;
  created_at: string;
  scenario: string;
  seed: number;
  duration: number;
  faults: { fault_id: string; type: string; target: string; start_time: number }[];
  result: Record<string, number | boolean>;
}

export interface Report {
  run_id: string;
  level: string;
  note: string;
  raw_metrics: Record<string, number | boolean>;
  score: {
    total: number;
    completion: number;
    time: number;
    safety: number;
    deductions: Record<string, number>;
  };
  faults: TimelineEvent[];
  safety_events: TimelineEvent[];
  anomaly_events: TimelineEvent[];
  world_events: TimelineEvent[];
  fault_to_detection_s: number | null;
}

export interface SeriesResponse {
  run_id: string;
  series: Record<string, { unit: string; t: number[]; v: number[] }>;
}

export interface CompareRow {
  sensor: string;
  unit: string;
  normal: { mean: number; std: number; hf_std: number; n: number };
  abnormal: { mean: number; std: number; hf_std: number; n: number };
  mean_shift_sigma: number;
  std_ratio: number;
  hf_ratio: number;
  deviation_score: number;
}

export interface CompareResponse {
  run_id: string;
  note: string;
  sensors: CompareRow[];
}
