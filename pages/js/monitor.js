// Online anomaly monitor: the browser counterpart of backend/app/anomaly/service.py plus the
// labeling (features/dataset.py) and metrics (evaluation/metrics.py) used for evaluation.
// Every 0.5 s of simulation time the latest 2 s window is featurized and scored by every
// detector. Output is advisory only and never reaches the safety layer (D-007).

import { FAST_CHANNELS, SLOW_CHANNELS, WINDOW, residualFeatures, scoreAll } from "./detect.js";

export const STRIDE_S = 0.5;
export const ALARM_CONSECUTIVE = 2;
const LABEL_MIN_OVERLAP = 0.5;
const POST_FAULT_GRACE_S = 10.0;
const HISTORY_FAST = 25 * 90; // 90 s of samples for charts

export class Monitor {
  constructor(bundle) {
    this.bundle = bundle;
    this.keys = Object.keys(bundle.detectors);
    this.reset();
  }
  reset() {
    this.buf = {};
    for (const k of [...FAST_CHANNELS, ...SLOW_CHANNELS]) this.buf[k] = [];
    this.lastT = -1e9;
    this.faults = [];
    this.windows = []; // {t, y, ignore, results}
    this.streak = Object.fromEntries(this.keys.map((k) => [k, 0]));
    this.alarm = Object.fromEntries(this.keys.map((k) => [k, false]));
    this.alerts = [];
    this.latest = null;
    this.mode = "auto"; // who is driving: "auto" (scripted operator) or "manual"
  }
  setFaults(faults) {
    this.faults = faults;
  }
  addFault(f) {
    this.faults.push(f);
  }
  /** Feed one simulator step ({fast, slow} sample dicts). Returns new alert events. */
  push(t, samples) {
    for (const block of [samples.fast, samples.slow]) {
      if (!block) continue;
      for (const [k, v] of Object.entries(block)) {
        const b = this.buf[k];
        if (!b) continue;
        b.push(v);
        if (b.length > HISTORY_FAST) b.splice(0, b.length - HISTORY_FAST);
      }
    }
    if (t - this.lastT < STRIDE_S - 1e-9) return [];
    this.lastT = t;
    const nf = WINDOW.n_fast, ns = WINDOW.n_slow;
    if (FAST_CHANNELS.some((k) => this.buf[k].length < nf) || SLOW_CHANNELS.some((k) => this.buf[k].length < ns)) return [];
    const fast = {}, slow = {};
    for (const k of FAST_CHANNELS) fast[k] = this.buf[k].slice(-nf);
    for (const k of SLOW_CHANNELS) slow[k] = this.buf[k].slice(-ns);
    const { x } = residualFeatures(fast, slow);
    const results = scoreAll(this.bundle, x);
    const events = [];
    for (const k of this.keys) {
      const r = results[k];
      r.sensors = r.is_anomaly ? this.bundle.detectors[k].explain(x) : [];
      this.streak[k] = r.is_anomaly ? this.streak[k] + 1 : 0;
      const alarm = this.streak[k] >= ALARM_CONSECUTIVE;
      if (alarm && !this.alarm[k]) {
        events.push({ kind: "anomaly", detector: k, time: t, score: r.score, threshold: r.threshold, sensors: r.sensors });
      } else if (!alarm && this.alarm[k] && this.streak[k] === 0) {
        events.push({ kind: "anomaly_cleared", detector: k, time: t });
      }
      this.alarm[k] = alarm;
      r.alarm = alarm;
    }
    const primary = this.bundle.primary;
    const lab = this.label(t);
    const w = { t, mode: this.mode, ...lab, results, sensors: results[primary].sensors };
    this.windows.push(w);
    if (this.windows.length > 4000) this.windows.shift();
    this.latest = w;
    this.alerts.push(...events);
    return events;
  }
  /** dataset.py labeling: >= 50 % of the window inside a fault = anomalous. */
  label(tEnd) {
    const tStart = tEnd - WINDOW.length_s;
    let y = 0, ignore = false, ftype = "";
    for (const f of this.faults) {
      const a = f.start_time, b = f.start_time + f.duration;
      const overlap = Math.max(0, Math.min(tEnd, b) - Math.max(tStart, a));
      const hit = overlap / WINDOW.length_s >= LABEL_MIN_OVERLAP;
      if (hit) { y = 1; ftype = f.type; }
      const partial = overlap > 0 && !hit;
      const grace = tStart >= b && tStart < b + POST_FAULT_GRACE_S;
      if ((partial || grace) && y === 0) ignore = true;
    }
    return { y, ignore, ftype };
  }
  /** Window-level confusion counts per detector (ignored windows excluded). */
  metrics(scope = "all") {
    const out = {};
    for (const k of this.keys) {
      const c = { tp: 0, fp: 0, fn: 0, tn: 0 };
      for (const w of this.windows) {
        if (w.ignore || (scope !== "all" && w.mode !== scope)) continue;
        const p = w.results[k].is_anomaly;
        if (p && w.y) c.tp++;
        else if (p) c.fp++;
        else if (w.y) c.fn++;
        else c.tn++;
      }
      const prec = c.tp + c.fp ? c.tp / (c.tp + c.fp) : null;
      const rec = c.tp + c.fn ? c.tp / (c.tp + c.fn) : null;
      c.precision = prec;
      c.recall = rec;
      c.f1 = prec && rec ? (2 * prec * rec) / (prec + rec) : prec === null || rec === null ? null : 0;
      c.fpr = c.fp + c.tn ? c.fp / (c.fp + c.tn) : null;
      out[k] = c;
    }
    return out;
  }
  /** metrics.py detection delay: first positive window with t_end in [start, end + 2 s]. */
  delays() {
    const out = {};
    for (const k of this.keys) {
      out[k] = this.faults.map((f) => {
        const a = f.start_time, b = f.start_time + f.duration;
        const hit = this.windows.find((w) => w.t >= a && w.t <= b + 2.0 && w.results[k].is_anomaly);
        return { fault_id: f.fault_id, detected: !!hit, delay_s: hit ? +(hit.t - a).toFixed(2) : null };
      });
    }
    return out;
  }
}
