// Window features + detectors, ported from ai/twinpilot_ai/features/windows.py
// ("residual" feature set) and ai/twinpilot_ai/anomaly/detectors.py / iforest.py.
// Detector parameters (means, limits, thresholds, Isolation Forest trees) are loaded from
// model/detector-v1-residual.json, exported by scripts/pages_demo/export_model.py, so the
// browser scores windows with exactly the bundle trained by the repo's Python code.

import { PARAMS } from "./sim.js";

export const JOINTS = ["swing_joint", "boom_joint", "arm_joint", "bucket_joint"];
export const FAST_CHANNELS = [
  ...JOINTS.flatMap((j) => ["position", "velocity", "effort", "command_error"].map((m) => `${j}.${m}`)),
  "left_track.velocity", "left_track.command", "right_track.velocity", "right_track.command",
  "imu.roll", "imu.pitch", "imu.angular_velocity_z", "imu.linear_accel_x", "imu.linear_accel_y", "imu.linear_accel_z",
];
export const SLOW_CHANNELS = ["hydraulic.oil_temperature", "engine.load", "chassis.vibration_rms"];
export const WINDOW = { length_s: 2.0, stride_s: 0.5, fast_hz: 25, slow_hz: 5, n_fast: 50, n_slow: 10 };
const TIME_CONSTANTS = Object.fromEntries(JOINTS.map((j) => [j, PARAMS.joints[j].time_constant_s]));
const TRACK_TAU = PARAMS.body.track_time_constant_s;
const FLAT_MIN_RUN = 3;
const STD_FLOOR = 1e-6;
const EULER = 0.5772156649015329;

const mean = (a) => { let s = 0; for (const v of a) s += v; return s / a.length; };
const absMean = (a) => { let s = 0; for (const v of a) s += Math.abs(v); return s / a.length; };
const absMax = (a) => { let m = -Infinity; for (const v of a) m = Math.max(m, Math.abs(v)); return m; };

function flatRun(x) {
  // longest run of identical consecutive samples / (n - 1), counted only when >= 3
  const n = x.length - 1;
  let run = 0, best = 0;
  for (let k = 0; k < n; k++) {
    run = x[k + 1] - x[k] === 0 ? run + 1 : 0;
    best = Math.max(best, run);
  }
  if (best < FLAT_MIN_RUN) best = 0;
  return best / n;
}

function hf(x) {
  // mean |second difference|
  let s = 0;
  for (let k = 0; k + 2 < x.length; k++) s += Math.abs(x[k + 2] - x[k + 1] - (x[k + 1] - x[k]));
  return s / (x.length - 2);
}

/** Measured velocity minus a nominal first-order actuator model driven by the command. */
export function twinResidual(cmd, vel, tau, dt) {
  const sub = 2;
  const a = Math.min(1.0, dt / sub / tau);
  let vHat = vel[0];
  const res = new Array(vel.length - 1);
  for (let k = 1; k < vel.length; k++) {
    for (let i = 0; i < sub; i++) vHat += (cmd[k - 1] - vHat) * a;
    res[k - 1] = vel[k] - vHat;
  }
  return res;
}

/**
 * fast: {channel: number[50]}, slow: {channel: number[10]} -> {x: number[83], names}
 * Same order as windows.features_batch(..., "residual").
 */
export function residualFeatures(fast, slow) {
  const x = [], names = [];
  const add = (v, n) => { x.push(v); names.push(n); };
  const dt = 1 / WINDOW.fast_hz;
  for (const j of JOINTS) {
    const ce = fast[`${j}.command_error`], vel = fast[`${j}.velocity`], pos = fast[`${j}.position`];
    add(absMean(ce), `${j}.command_error:absmean`);
    add(absMax(ce), `${j}.command_error:absmax`);
    const cmd = vel.map((v, i) => v + ce[i]);
    const r = twinResidual(cmd, vel, TIME_CONSTANTS[j], dt);
    add(absMean(r), `${j}.velocity:twin_absmean`);
    add(absMax(r), `${j}.velocity:twin_absmax`);
    add(mean(r), `${j}.velocity:twin_mean`);
    let dpos = pos[pos.length - 1] - pos[0];
    if (j === "swing_joint") {
      let m = (dpos + Math.PI) % (2 * Math.PI);
      if (m < 0) m += 2 * Math.PI;
      dpos = m - Math.PI;
    }
    let vs = 0;
    for (let k = 1; k < vel.length; k++) vs += vel[k];
    add(Math.abs(dpos - vs * dt), `${j}.position:kin_residual`);
  }
  for (const tr of ["left_track", "right_track"]) {
    const r = twinResidual(fast[`${tr}.command`], fast[`${tr}.velocity`], TRACK_TAU, dt);
    add(absMean(r), `${tr}.velocity:twin_absmean`);
  }
  for (const [block, chans] of [[fast, FAST_CHANNELS], [slow, SLOW_CHANNELS]]) {
    for (const c of chans) {
      if (c.endsWith(".command")) continue;
      add(flatRun(block[c]), `${c}:flat`);
      add(hf(block[c]), `${c}:hf`);
    }
  }
  for (const c of SLOW_CHANNELS) {
    const s = slow[c];
    add((s[s.length - 1] - s[0]) / WINDOW.length_s, `${c}:slope`);
  }
  return { x, names };
}

export const sensorOf = (name) => name.split(":")[0];

// ------------------------------------------------------------------ detectors
function cFactor(n) {
  if (n > 2) return 2.0 * (Math.log(n - 1.0) + EULER) - (2.0 * (n - 1.0)) / n;
  return n === 2 ? 1.0 : 0.0;
}

function topSensors(values, names, k = 3) {
  const order = values.map((v, i) => i).sort((a, b) => values[b] - values[a]);
  const out = [];
  for (const i of order) {
    const s = sensorOf(names[i]);
    if (!out.includes(s)) out.push(s);
    if (out.length === k) break;
  }
  return out;
}

class Detector {
  constructor(key, d, names) {
    this.key = key;
    this.d = d;
    this.names = names;
    this.threshold = d.threshold;
    this.modelVersion = d.model_version;
    this.sd = d.sd.map((v) => Math.max(v, STD_FLOOR)); // already floored in Python; idempotent
  }
  absZ(x) {
    return x.map((v, i) => Math.abs((v - this.d.mu[i]) / this.sd[i]));
  }
  predict(x) {
    return this.score(x) > this.threshold;
  }
  explain(x, k = 3) {
    return topSensors(this.absZ(x), this.names, k);
  }
}

/** Rule baseline: fixed per-feature limits (p0.5 / p99.5 of normal windows). */
class ThresholdDetector extends Detector {
  exceedance(x) {
    const { max_idx, min_idx, hi, lo, span } = this.d;
    return max_idx.map((mi, k) => Math.max(Math.max((x[mi] - hi[k]) / span[k], (lo[k] - x[min_idx[k]]) / span[k]), 0.0));
  }
  score(x) {
    return Math.max(...this.exceedance(x));
  }
  explain(x, k = 3) {
    const e = this.exceedance(x);
    if (Math.max(...e) <= 0) return super.explain(x, k);
    const order = e.map((v, i) => i).sort((a, b) => e[b] - e[a]).slice(0, k);
    return order.filter((i) => e[i] > 0).map((i) => sensorOf(this.names[this.d.max_idx[i]]));
  }
}

class ZScoreDetector extends Detector {
  score(x) {
    return Math.max(...this.absZ(x));
  }
}

class TailZDetector extends Detector {
  normalized(x) {
    return this.absZ(x).map((v, i) => v / this.d.tail[i]);
  }
  score(x) {
    return Math.max(...this.normalized(x));
  }
  explain(x, k = 3) {
    return topSensors(this.normalized(x), this.names, k);
  }
}

/** Isolation Forest scored with the exported trees (Liu et al. score in (0, 1]). */
class IsolationForestDetector extends Detector {
  constructor(key, d, names) {
    super(key, d, names);
    this.c = cFactor(d.psi);
  }
  pathLength(x) {
    const { scaler_mean: m, scaler_scale: s, trees } = this.d;
    const z = x.map((v, i) => (v - m[i]) / s[i]);
    let total = 0;
    for (const t of trees) {
      let node = 0;
      while (t.f[node] >= 0) node = z[t.f[node]] < t.t[node] ? t.l[node] : t.r[node];
      total += t.v[node];
    }
    return total / trees.length;
  }
  score(x) {
    return Math.pow(2.0, -this.pathLength(x) / this.c);
  }
}

const CLASSES = {
  threshold: ThresholdDetector,
  zscore: ZScoreDetector,
  "zscore-tail": TailZDetector,
  "isolation-forest": IsolationForestDetector,
};

export function loadBundle(json) {
  const detectors = {};
  for (const [k, d] of Object.entries(json.detectors)) detectors[k] = new CLASSES[k](k, d, json.feature_names);
  return { ...json, detectors };
}

/** Score one feature vector with every detector. */
export function scoreAll(bundle, x) {
  const out = {};
  for (const [k, det] of Object.entries(bundle.detectors)) {
    const score = det.score(x);
    out[k] = { score, threshold: det.threshold, is_anomaly: score > det.threshold };
  }
  return out;
}
