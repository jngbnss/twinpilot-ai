// JavaScript port of the deterministic excavator simulator (sim/twinpilot_sim).
// Level 1 simulation only: no real machine or sensor is connected.
// Ported 1:1 from simulator.py, kinematics.py, faults.py, world.py, control/safety.py,
// control/mapper.py and operator.py. The only intended difference is the random number
// generator (NumPy PCG64 -> seeded xorshift + Box-Muller), so noise sequences differ from
// Python but every equation, constant and rule is the same. scripts/pages_demo checks the
// noise-free trajectories against the Python simulator.

// ------------------------------------------------------------------ parameters
// sim/twinpilot_sim/params/excavator.yaml
export const PARAMS = {
  body: {
    mass_kg: 20000.0,
    track_gauge_m: 2.4,
    max_track_speed_mps: 1.5,
    track_time_constant_s: 0.4,
    cg_height_m: 1.1,
    half_track_length_m: 1.8,
    half_track_width_m: 1.5,
    footprint_radius_m: 2.8,
  },
  boom_pivot: { x_m: 0.5, z_m: 2.0 },
  links: {
    boom: { length_m: 5.7, mass_kg: 1800.0 },
    arm: { length_m: 2.9, mass_kg: 900.0 },
    bucket: { length_m: 1.4, mass_kg: 800.0 },
  },
  joints: {
    swing_joint: { type: "continuous", max_velocity_rps: 1.0, time_constant_s: 0.35, viscous_friction: 4000.0, inertia_kgm2: 60000.0, initial_rad: 0.0 },
    boom_joint: { type: "revolute", lower_rad: -0.5, upper_rad: 1.2, max_velocity_rps: 0.45, time_constant_s: 0.25, viscous_friction: 6000.0, initial_rad: 0.6 },
    arm_joint: { type: "revolute", lower_rad: -2.6, upper_rad: -0.5, max_velocity_rps: 0.6, time_constant_s: 0.2, viscous_friction: 3000.0, initial_rad: -1.6 },
    bucket_joint: { type: "revolute", lower_rad: -1.0, upper_rad: 1.8, max_velocity_rps: 1.0, time_constant_s: 0.15, viscous_friction: 1200.0, initial_rad: 0.0 },
  },
  grasp: { attach_radius_m: 1.0, close_threshold_rad: 0.9, release_threshold_rad: 0.4 },
  hydraulic: { ambient_c: 25.0, initial_c: 45.0, heat_per_kw: 0.002, base_heat_cps: 0.08, cooling_coeff: 0.004 },
  engine: { rated_power_kw: 110.0, idle_power_kw: 12.0 },
  vibration: { base_rms: 0.15, per_kw: 0.006 },
  sensors: {
    fast_rate_hz: 25,
    slow_rate_hz: 5,
    noise: { position_rad: 0.0005, velocity_rps: 0.002, effort_nm: 150.0, imu_angle_rad: 0.0008, imu_rate_rps: 0.002, accel_mps2: 0.03, temperature_c: 0.05, pose_m: 0.01 },
  },
  safety: { max_roll_rad: 0.2, max_pitch_rad: 0.25, worker_min_distance_m: 5.0, command_timeout_s: 0.5, max_axis_rate_per_s: 4.0, limit_margin_rad: 0.02 },
};

// sim/twinpilot_sim/scenarios/excavator_carry.yaml
export const TASK = {
  name: "excavator_carry",
  time_limit_s: 300.0,
  target_count: 3,
  start_pose: { x: 0.0, y: -6.0, yaw: 1.5707963 },
  work_position: { x: 0.0, y: 0.0 },
  zone_a: { name: "zone_a", x: -6.0, y: 2.0, radius: 2.5 },
  zone_b: { name: "zone_b", x: 6.0, y: 2.0, radius: 2.5 },
  forbidden_zones: [{ name: "forbidden_north", xmin: -3.0, xmax: 3.0, ymin: 7.0, ymax: 11.0 }],
  worker: { x: 11.0, y: -7.0, radius: 0.4 },
  drop_height_m: 2.5,
  objects: [
    { object_id: "box_1", kind: "box", mass_kg: 300.0, size_m: 0.8, x: -6.8, y: 1.4 },
    { object_id: "rock_1", kind: "rock", mass_kg: 450.0, size_m: 0.8, x: -5.3, y: 1.9 },
    { object_id: "soil_1", kind: "soil_block", mass_kg: 600.0, size_m: 0.8, x: -6.3, y: 3.1 },
  ],
};

export const JOINTS = ["swing_joint", "boom_joint", "arm_joint", "bucket_joint"];
export const TRACKS = ["left_track", "right_track"];
export const AXES = ["left_track", "right_track", "swing", "boom", "arm", "bucket"];
export const AXIS_TO_JOINT = { swing: "swing_joint", boom: "boom_joint", arm: "arm_joint", bucket: "bucket_joint" };

export const G = 9.81;
export const DT = 0.02;
const ROLLING_RESISTANCE = 0.1;
const TILT_GAIN = 0.4;
const HYDRAULIC_EFFICIENCY = 0.8;
const TAU = 2 * Math.PI;

// ------------------------------------------------------------------ helpers
/** Python float modulo semantics: (a + pi) % (2 pi) - pi */
export function wrapAngle(a) {
  let m = (a + Math.PI) % TAU;
  if (m !== 0 && m < 0) m += TAU;
  return m - Math.PI;
}
const clip = (v) => Math.max(-1.0, Math.min(1.0, v));
const hypot = (x, y) => Math.sqrt(x * x + y * y);
const dist3 = (a, b) => Math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2);
/** Python round(x, 6) for telemetry values (ties are vanishingly rare). */
export const round6 = (v) => Number(v.toFixed(6));

/** Seeded RNG (splitmix-style seeding + sfc32) with Box-Muller normals. Replaces NumPy PCG64. */
export class Rng {
  constructor(seed) {
    let s = (seed >>> 0) ^ 0x9e3779b9;
    const sm = () => {
      s = (s + 0x6d2b79f5) >>> 0;
      let t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    this.spare = null;
    this._s = Array.from({ length: 4 }, () => (sm() * 4294967296) >>> 0);
  }
  random() {
    // sfc32
    let [a, b, c, d] = this._s;
    const t = (((a + b) >>> 0) + d) >>> 0;
    d = (d + 1) >>> 0;
    a = b ^ (b >>> 9);
    b = (c + (c << 3)) >>> 0;
    c = (c << 21) | (c >>> 11);
    c = (c + t) >>> 0;
    this._s = [a, b, c, d];
    return t / 4294967296;
  }
  uniform(lo, hi) {
    return lo + (hi - lo) * this.random();
  }
  normal(mu, std) {
    if (std === 0) return mu; // keeps zero-noise runs exactly deterministic
    let z;
    if (this.spare !== null) {
      z = this.spare;
      this.spare = null;
    } else {
      let u = 0;
      while (u === 0) u = this.random();
      const v = this.random();
      const r = Math.sqrt(-2 * Math.log(u));
      z = r * Math.cos(TAU * v);
      this.spare = r * Math.sin(TAU * v);
    }
    return mu + std * z;
  }
}

// ------------------------------------------------------------------ kinematics
export function frontPose(p, boom, arm, bucket) {
  const lb = p.links.boom.length_m, la = p.links.arm.length_m, lk = p.links.bucket.length_m;
  const p0 = { r: p.boom_pivot.x_m, z: p.boom_pivot.z_m };
  const p1 = { r: p0.r + lb * Math.cos(boom), z: p0.z + lb * Math.sin(boom) };
  const a = boom + arm;
  const p2 = { r: p1.r + la * Math.cos(a), z: p1.z + la * Math.sin(a) };
  const phi = a - bucket;
  const bc = { r: p2.r + 0.5 * lk * Math.cos(phi), z: p2.z + 0.5 * lk * Math.sin(phi) };
  const p3 = { r: p2.r + lk * Math.cos(phi), z: p2.z + lk * Math.sin(phi) };
  return { pivot: p0, boom_tip: p1, arm_tip: p2, bucket_center: bc, bucket_tip: p3, phi };
}

export function frontMoment(p, boom, arm, bucket, payload) {
  const fp = frontPose(p, boom, arm, bucket);
  const lk = p.links;
  return (
    (lk.boom.mass_kg * (fp.pivot.r + fp.boom_tip.r)) / 2 +
    (lk.arm.mass_kg * (fp.boom_tip.r + fp.arm_tip.r)) / 2 +
    (lk.bucket.mass_kg + payload) * fp.bucket_center.r
  );
}

export function toWorld(pt, bx, by, yaw, swing) {
  const h = yaw + swing;
  return [bx + pt.r * Math.cos(h), by + pt.r * Math.sin(h), pt.z];
}

export function armIk(p, r, z) {
  const lb = p.links.boom.length_m, la = p.links.arm.length_m;
  const dx = r - p.boom_pivot.x_m, dz = z - p.boom_pivot.z_m;
  const d = (dx * dx + dz * dz - lb * lb - la * la) / (2 * lb * la);
  if (!(d >= -1.0 && d <= 1.0)) return null;
  const arm = -Math.acos(d);
  const boom = Math.atan2(dz, dx) - Math.atan2(la * Math.sin(arm), lb + la * Math.cos(arm));
  return [boom, arm];
}

export function bucketCenterIk(p, r, z, bucket, iterations = 4) {
  const half = 0.5 * p.links.bucket.length_m;
  let sol = armIk(p, r, z + half);
  for (let i = 0; i < iterations; i++) {
    if (sol === null) return null;
    const phi = sol[0] + sol[1] - bucket;
    sol = armIk(p, r - half * Math.cos(phi), z - half * Math.sin(phi));
  }
  return sol;
}

// ------------------------------------------------------------------ faults
export const FAULT_TYPES = ["JOINT_LAG", "FRICTION_INCREASE", "SENSOR_NOISE", "SENSOR_STUCK", "TEMP_RISE"];
const FAULT_RNG_OFFSET = 10007;
// faults.py NOISE_SCALE: noise std at severity 1.0 per metric (SENSOR_NOISE)
export const NOISE_SCALE = {
  position: 0.03, velocity: 0.08, effort: 4000.0, command_error: 0.08, roll: 0.02, pitch: 0.02, yaw: 0.02,
  angular_velocity_z: 0.05, linear_accel_x: 0.6, linear_accel_y: 0.6, linear_accel_z: 0.8,
  oil_temperature: 2.0, vibration_rms: 0.4, load: 6.0, pose_x: 0.2, pose_y: 0.2,
};

export const faultActive = (f, t) => f.start_time <= t && t < f.start_time + f.duration;

export class FaultInjector {
  constructor(faults = [], seed = 0) {
    this.faults = [...faults];
    this.rng = new Rng(seed + FAULT_RNG_OFFSET);
    this.stuck = new Map();
  }
  add(f) {
    this.faults.push(f);
  }
  active(t) {
    return this.faults.filter((f) => faultActive(f, t));
  }
  jointFactors(joint, t) {
    let lag = 1.0, fric = 1.0;
    for (const f of this.faults) {
      if (f.target !== joint || !faultActive(f, t)) continue;
      if (f.type === "JOINT_LAG") lag *= 1.0 + 4.0 * f.severity;
      else if (f.type === "FRICTION_INCREASE") fric *= 1.0 + 3.0 * f.severity;
    }
    return [lag, fric];
  }
  temperatureRate(t) {
    let rate = 0.0;
    for (const f of this.faults) {
      if (!faultActive(f, t)) continue;
      if (f.type === "TEMP_RISE") rate += 0.5 * f.severity;
      else if (f.type === "FRICTION_INCREASE") rate += 0.05 * f.severity;
    }
    return rate;
  }
  corrupt(key, value, t) {
    for (const f of this.faults) {
      if (f.target !== key) continue;
      if (f.type === "SENSOR_STUCK") {
        if (faultActive(f, t)) {
          if (!this.stuck.has(f.fault_id)) this.stuck.set(f.fault_id, value);
          return this.stuck.get(f.fault_id);
        }
      } else if (f.type === "SENSOR_NOISE" && faultActive(f, t)) {
        const metric = key.split(".").slice(1).join(".");
        const scale = metric in NOISE_SCALE ? NOISE_SCALE[metric] : 0.05 * Math.abs(value) + 0.01;
        value += this.rng.normal(0.0, scale * f.severity);
      }
    }
    return value;
  }
}

// ------------------------------------------------------------------ world
const inCircle = (z, x, y) => hypot(x - z.x, y - z.y) <= z.radius;
export function rectDistance(z, x, y) {
  const dx = Math.max(z.xmin - x, 0.0, x - z.xmax);
  const dy = Math.max(z.ymin - y, 0.0, y - z.ymax);
  return hypot(dx, dy);
}

export class World {
  constructor(task, attachRadius, closeTh, releaseTh) {
    this.task = task;
    this.attachRadius = attachRadius;
    this.closeTh = closeTh;
    this.releaseTh = releaseTh;
    this.reset();
  }
  reset() {
    this.objects = this.task.objects.map((o) => ({
      object_id: o.object_id, kind: o.kind, mass_kg: o.mass_kg, size_m: o.size_m,
      x: o.x, y: o.y, z: o.size_m / 2, state: "resting",
    }));
    this.counters = { delivered: 0, drops: 0, collisions: 0, worker_collisions: 0, worker_proximity: 0, forbidden_entries: 0, tipover_risks: 0, completed_at: null };
    this.flags = {};
  }
  get payload() {
    return this.objects.find((o) => o.state === "attached") ?? null;
  }
  get payloadMass() {
    const p = this.payload;
    return p ? p.mass_kg : 0.0;
  }
  edge(key, active) {
    const prev = this.flags[key] ?? false;
    this.flags[key] = active;
    return active && !prev;
  }
  update(t, base, footR, bucketCenter, bucketPrev, bucketNow, tiltExceeded, workerMin) {
    const ev = [];
    const [bx, by, bz] = bucketCenter;
    let payload = this.payload;
    if (payload === null && bucketPrev < this.closeTh && this.closeTh <= bucketNow) {
      let best = null, bestD = this.attachRadius;
      for (const o of this.objects) {
        if (o.state !== "resting") continue;
        const d = dist3([o.x, o.y, o.z], bucketCenter);
        if (d <= bestD) { best = o; bestD = d; }
      }
      if (best !== null) {
        best.state = "attached";
        ev.push({ type: "attach", time: t, detail: { object_id: best.object_id } });
      }
    } else if (payload !== null && bucketNow < this.releaseTh) {
      payload.x = bx; payload.y = by; payload.z = payload.size_m / 2;
      const high = bz > this.task.drop_height_m;
      if (inCircle(this.task.zone_b, bx, by)) {
        payload.state = "delivered";
        this.counters.delivered += 1;
        ev.push({ type: "delivered", time: t, detail: { object_id: payload.object_id } });
        if (this.counters.delivered >= this.task.target_count) {
          this.counters.completed_at = t;
          ev.push({ type: "task_complete", time: t, detail: {} });
        }
      } else {
        payload.state = "resting";
      }
      if (high || !(inCircle(this.task.zone_b, bx, by) || inCircle(this.task.zone_a, bx, by))) {
        this.counters.drops += 1;
        ev.push({ type: "drop", time: t, detail: { object_id: payload.object_id } });
      } else {
        ev.push({ type: "release", time: t, detail: { object_id: payload.object_id } });
      }
    }
    payload = this.payload;
    if (payload !== null) { payload.x = bx; payload.y = by; payload.z = bz; }

    const hit = this.objects.some((o) => o.state !== "attached" && hypot(o.x - base[0], o.y - base[1]) < footR + o.size_m / 2);
    if (this.edge("collision", hit)) { this.counters.collisions += 1; ev.push({ type: "collision", time: t, detail: {} }); }

    const w = this.task.worker;
    const dBody = hypot(w.x - base[0], w.y - base[1]) - footR;
    const dBucket = hypot(w.x - bx, w.y - by);
    if (this.edge("worker_collision", dBody < w.radius || dBucket < w.radius + 0.5)) {
      this.counters.worker_collisions += 1;
      ev.push({ type: "worker_collision", time: t, detail: {} });
    }
    if (this.edge("worker_proximity", Math.min(dBody, dBucket) < workerMin)) {
      this.counters.worker_proximity += 1;
      ev.push({ type: "worker_proximity", time: t, detail: { distance: Math.min(dBody, dBucket) } });
    }
    const inside = this.task.forbidden_zones.some((z) => rectDistance(z, base[0], base[1]) <= footR);
    if (this.edge("forbidden", inside)) { this.counters.forbidden_entries += 1; ev.push({ type: "forbidden_zone_entry", time: t, detail: {} }); }
    if (this.edge("tipover", tiltExceeded)) { this.counters.tipover_risks += 1; ev.push({ type: "tipover_risk", time: t, detail: {} }); }
    return ev;
  }
  workerDistance(base, footR, bucketXY) {
    const w = this.task.worker;
    const dBody = hypot(w.x - base[0], w.y - base[1]) - footR;
    return Math.min(dBody, hypot(w.x - bucketXY[0], w.y - bucketXY[1]));
  }
}

// ------------------------------------------------------------------ simulator
const noAxes = () => ({ left_track: 0, right_track: 0, swing: 0, boom: 0, arm: 0, bucket: 0 });
export const actuator = (axes) => {
  const out = {};
  for (const a of AXES) out[a] = clip(Number(axes[a] ?? 0.0));
  return out;
};

export class Simulator {
  constructor({ seed = 0, params = PARAMS, task = TASK, faults = null, payloadScale = 1.0 } = {}) {
    this.params = params;
    this.task = task;
    this.faults = faults ?? new FaultInjector([], seed);
    this.payloadScale = payloadScale;
    const p = params;
    this.fastEvery = Math.max(1, Math.round(1 / (DT * p.sensors.fast_rate_hz)));
    this.slowEvery = Math.max(1, Math.round(1 / (DT * p.sensors.slow_rate_hz)));
    this.world = new World(task, p.grasp.attach_radius_m, p.grasp.close_threshold_rad, p.grasp.release_threshold_rad);
    this.nominalMoment = frontMoment(p, p.joints.boom_joint.initial_rad, p.joints.arm_joint.initial_rad, p.joints.bucket_joint.initial_rad, 0.0);
    this.reset(seed);
  }
  reset(seed) {
    const p = this.params;
    this.seed = seed;
    this.rng = new Rng(seed);
    this.tick = 0;
    this.t = 0.0;
    const sp = this.task.start_pose;
    this.x = sp.x; this.y = sp.y; this.yaw = sp.yaw;
    this.vTrack = [0.0, 0.0];
    this.trackCmd = [0.0, 0.0];
    this.prevSpeed = 0.0;
    this.accelFwd = 0.0;
    this.omega = 0.0;
    this.q = {}; this.qd = {}; this.qdCmd = {}; this.effort = {};
    for (const j of JOINTS) { this.q[j] = p.joints[j].initial_rad; this.qd[j] = 0; this.qdCmd[j] = 0; this.effort[j] = 0; }
    this.temp = p.hydraulic.initial_c;
    this.energyKj = 0.0;
    this.hydKw = 0.0;
    this.engineKw = p.engine.idle_power_kw;
    this.roll = 0.0;
    this.pitch = 0.0;
    this.world.reset();
    this.events = [];
  }
  payloadMass() {
    return this.world.payloadMass * this.payloadScale;
  }
  gravityTorques(payload) {
    const p = this.params;
    const fp = frontPose(p, this.q.boom_joint, this.q.arm_joint, this.q.bucket_joint);
    const lk = p.links;
    const boomC = (fp.pivot.r + fp.boom_tip.r) / 2;
    const armC = (fp.boom_tip.r + fp.arm_tip.r) / 2;
    const bc = fp.bucket_center.r;
    const mb = lk.bucket.mass_kg + payload;
    return {
      boom_joint: G * (lk.boom.mass_kg * (boomC - fp.pivot.r) + lk.arm.mass_kg * (armC - fp.pivot.r) + mb * (bc - fp.pivot.r)),
      arm_joint: G * (lk.arm.mass_kg * (armC - fp.boom_tip.r) + mb * (bc - fp.boom_tip.r)),
      bucket_joint: G * mb * (bc - fp.arm_tip.r),
    };
  }
  stepTracks(cmd) {
    const b = this.params.body;
    this.trackCmd = [cmd.left_track * b.max_track_speed_mps, cmd.right_track * b.max_track_speed_mps];
    const alpha = DT / b.track_time_constant_s;
    for (let i = 0; i < 2; i++) this.vTrack[i] += (this.trackCmd[i] - this.vTrack[i]) * alpha;
    const [vl, vr] = this.vTrack;
    const v = (vl + vr) / 2;
    this.omega = (vr - vl) / b.track_gauge_m;
    this.yaw = wrapAngle(this.yaw + this.omega * DT);
    this.x += v * Math.cos(this.yaw) * DT;
    this.y += v * Math.sin(this.yaw) * DT;
    this.accelFwd = (v - this.prevSpeed) / DT;
    this.prevSpeed = v;
  }
  stepJoints(cmd) {
    const p = this.params;
    const payload = this.payloadMass();
    const grav = this.gravityTorques(payload);
    for (const [axis, j] of Object.entries(AXIS_TO_JOINT)) {
      const jp = p.joints[j];
      const [lag, fric] = this.faults.jointFactors(j, this.t);
      const vCmd = cmd[axis] * jp.max_velocity_rps;
      this.qdCmd[j] = vCmd;
      const vTarget = vCmd / (1.0 + 0.6 * (fric - 1.0));
      const tau = jp.time_constant_s * lag;
      const vPrev = this.qd[j];
      let vNew = vPrev + (vTarget - vPrev) * Math.min(1.0, DT / tau);
      let qNew = this.q[j] + vNew * DT;
      if (jp.type === "revolute") {
        const lo = jp.lower_rad, hi = jp.upper_rad;
        if (qNew <= lo || qNew >= hi) {
          qNew = Math.min(Math.max(qNew, lo), hi);
          vNew = 0.0;
        }
      } else {
        qNew = wrapAngle(qNew);
      }
      const accel = (vNew - vPrev) / DT;
      let effort;
      if (j === "swing_joint") {
        const reach = frontMoment(p, this.q.boom_joint, this.q.arm_joint, this.q.bucket_joint, payload) * 6.0;
        const inertia = (jp.inertia_kgm2 ?? 0.0) + reach;
        effort = inertia * accel + jp.viscous_friction * fric * vNew;
      } else {
        effort = grav[j] + jp.viscous_friction * fric * vNew + 2000.0 * accel;
      }
      this.q[j] = qNew; this.qd[j] = vNew; this.effort[j] = effort;
    }
  }
  stepMachineState() {
    const p = this.params;
    let jointW = 0;
    for (const j of JOINTS) jointW += Math.abs(this.effort[j] * this.qd[j]);
    const trackW = (ROLLING_RESISTANCE * p.body.mass_kg * G * (Math.abs(this.vTrack[0]) + Math.abs(this.vTrack[1]))) / 2;
    this.hydKw = (jointW + trackW) / 1000.0;
    this.engineKw = Math.min(p.engine.rated_power_kw, p.engine.idle_power_kw + this.hydKw / HYDRAULIC_EFFICIENCY);
    this.energyKj += this.engineKw * DT;
    const h = p.hydraulic;
    const dtemp = h.base_heat_cps + h.heat_per_kw * this.hydKw - h.cooling_coeff * (this.temp - h.ambient_c) + this.faults.temperatureRate(this.t);
    this.temp += dtemp * DT;
    const payload = this.payloadMass();
    const m = frontMoment(p, this.q.boom_joint, this.q.arm_joint, this.q.bucket_joint, payload);
    const shift = (m - this.nominalMoment) / (p.body.mass_kg + payload);
    const sw = this.q.swing_joint;
    const fwd = shift * Math.cos(sw), lat = shift * Math.sin(sw);
    this.pitch = (TILT_GAIN * fwd) / p.body.half_track_length_m - 0.01 * this.accelFwd;
    this.roll = (TILT_GAIN * lat) / p.body.half_track_width_m;
  }
  /** One 20 ms step. Returns {fast, slow}: sensor samples as {key: value} or null. */
  step(command) {
    const bucketPrev = this.q.bucket_joint;
    this.stepTracks(command);
    this.stepJoints(command);
    this.stepMachineState();
    this.tick += 1;
    this.t = this.tick * DT;
    const fw = this.frontWorld();
    const s = this.params.safety;
    this.events = this.world.update(
      this.t, [this.x, this.y], this.params.body.footprint_radius_m, fw.bucket_center, bucketPrev,
      this.q.bucket_joint, Math.abs(this.roll) > s.max_roll_rad || Math.abs(this.pitch) > s.max_pitch_rad,
      s.worker_min_distance_m,
    );
    const out = { fast: null, slow: null };
    if (this.tick % this.fastEvery === 0) out.fast = this.emitFast();
    if (this.tick % this.slowEvery === 0) out.slow = this.emitSlow();
    return out;
  }
  frontWorld() {
    const fp = frontPose(this.params, this.q.boom_joint, this.q.arm_joint, this.q.bucket_joint);
    const sw = this.q.swing_joint;
    return {
      bucket_center: toWorld(fp.bucket_center, this.x, this.y, this.yaw, sw),
      bucket_tip: toWorld(fp.bucket_tip, this.x, this.y, this.yaw, sw),
    };
  }
  noise(std) {
    return this.rng.normal(0.0, std);
  }
  emit(out, key, value) {
    out[key] = round6(this.faults.corrupt(key, value, this.t));
  }
  emitFast() {
    const n = this.params.sensors.noise;
    const out = {};
    for (const j of JOINTS) {
      const vel = this.qd[j] + this.noise(n.velocity_rps);
      this.emit(out, `${j}.position`, this.q[j] + this.noise(n.position_rad));
      this.emit(out, `${j}.velocity`, vel);
      this.emit(out, `${j}.effort`, this.effort[j] + this.noise(n.effort_nm));
      this.emit(out, `${j}.command`, this.qdCmd[j]);
      this.emit(out, `${j}.command_error`, this.qdCmd[j] - vel);
    }
    TRACKS.forEach((tr, i) => {
      this.emit(out, `${tr}.velocity`, this.vTrack[i] + this.noise(n.velocity_rps));
      this.emit(out, `${tr}.command`, this.trackCmd[i]);
    });
    const v = (this.vTrack[0] + this.vTrack[1]) / 2;
    this.emit(out, "imu.roll", this.roll + this.noise(n.imu_angle_rad));
    this.emit(out, "imu.pitch", this.pitch + this.noise(n.imu_angle_rad));
    this.emit(out, "imu.yaw", wrapAngle(this.yaw + this.noise(n.imu_angle_rad)));
    this.emit(out, "imu.angular_velocity_z", this.omega + this.noise(n.imu_rate_rps));
    const vib = this.vibration();
    this.emit(out, "imu.linear_accel_x", this.accelFwd + this.noise(n.accel_mps2 + vib * 0.3));
    this.emit(out, "imu.linear_accel_y", v * this.omega + this.noise(n.accel_mps2 + vib * 0.3));
    this.emit(out, "imu.linear_accel_z", G + this.noise(n.accel_mps2 + vib * 0.5));
    this.emit(out, "base.pose_x", this.x + this.noise(n.pose_m));
    this.emit(out, "base.pose_y", this.y + this.noise(n.pose_m));
    return out;
  }
  vibration() {
    const vp = this.params.vibration;
    const speed = Math.abs(this.vTrack[0]) + Math.abs(this.vTrack[1]);
    return vp.base_rms + vp.per_kw * this.hydKw + 0.2 * speed;
  }
  emitSlow() {
    const n = this.params.sensors.noise;
    const p = this.params;
    const out = {};
    const load = (100.0 * this.engineKw) / p.engine.rated_power_kw;
    this.emit(out, "hydraulic.oil_temperature", this.temp + this.noise(n.temperature_c));
    this.emit(out, "engine.load", load + this.noise(0.3));
    const vib = this.vibration();
    this.emit(out, "chassis.vibration_rms", Math.max(0.0, vib + this.noise(0.02 + 0.05 * vib)));
    this.emit(out, "energy.consumed", this.energyKj);
    this.emit(out, "contact.bucket_payload", this.world.payload ? 1.0 : 0.0);
    this.emit(out, "contact.payload_mass", this.payloadMass());
    this.emit(out, "task.delivered", this.world.counters.delivered);
    return out;
  }
  groundTruth() {
    const fw = this.frontWorld();
    const bc = fw.bucket_center;
    const fr = this.params.body.footprint_radius_m;
    const payload = this.world.payload;
    const joints = {};
    for (const j of JOINTS) joints[j] = { position: this.q[j], velocity: this.qd[j], effort: this.effort[j], command: this.qdCmd[j] };
    return {
      sim_time: this.t, base_x: this.x, base_y: this.y, yaw: this.yaw, roll: this.roll, pitch: this.pitch,
      linear_speed: (this.vTrack[0] + this.vTrack[1]) / 2, track_speeds: [...this.vTrack], joints,
      bucket_center: bc, bucket_tip: fw.bucket_tip, footprint_radius: fr,
      worker_distance: this.world.workerDistance([this.x, this.y], fr, [bc[0], bc[1]]),
      payload_id: payload ? payload.object_id : null, payload_mass: this.payloadMass(),
      objects: this.world.objects.map((o) => ({ ...o })), events: [...this.events],
    };
  }
}

// ------------------------------------------------------------------ control mapper
// control/mapper.py KEY_BINDINGS: key -> [axis, direction]
export const KEY_BINDINGS = {
  w: ["drive", 1.0], s: ["drive", -1.0], a: ["steer", -1.0], d: ["steer", 1.0],
  q: ["swing", 1.0], e: ["swing", -1.0], r: ["boom", 1.0], f: ["boom", -1.0],
  t: ["arm", 1.0], g: ["arm", -1.0], y: ["bucket", -1.0], h: ["bucket", 1.0],
};
export const ESTOP_KEY = " ";

export function tracksFrom(drive, steer) {
  return [clip(drive + steer), clip(drive - steer)];
}

export function keyboardToCommand(pressed, sequence, timestamp, scale = 1.0) {
  const acc = { drive: 0, steer: 0, swing: 0, boom: 0, arm: 0, bucket: 0 };
  const keys = new Set([...pressed].map((k) => (k !== ESTOP_KEY ? k.toLowerCase() : k)));
  for (const k of keys) {
    if (k in KEY_BINDINGS) {
      const [axis, sign] = KEY_BINDINGS[k];
      acc[axis] += sign;
    }
  }
  const [left, right] = tracksFrom(acc.drive * scale, acc.steer * scale);
  return {
    timestamp, device: "keyboard", sequence, left_track: left, right_track: right,
    swing: clip(acc.swing * scale), boom: clip(acc.boom * scale), arm: clip(acc.arm * scale), bucket: clip(acc.bucket * scale),
    estop: keys.has(ESTOP_KEY),
  };
}

export class ControlMapper {
  constructor() {
    this.lastSequence = {};
    this.rejected = 0;
    this.accepted = 0;
  }
  accept(cmd) {
    const prev = this.lastSequence[cmd.device];
    if (prev !== undefined && cmd.sequence <= prev) {
      this.rejected += 1;
      return false;
    }
    this.lastSequence[cmd.device] = cmd.sequence;
    this.accepted += 1;
    return true;
  }
}

// ------------------------------------------------------------------ safety layer
// control/safety.py — deterministic, never reads anomaly-detection output (D-007).
export const RISK = ["INFO", "CAUTION", "WARNING", "EMERGENCY"];
const WORK_AXES = ["boom", "arm", "bucket"];
const PREDICT_S = 0.5;
const PROBE = 0.05;
const isClose = (a, b) => a === b || Math.abs(a - b) <= 1e-9 * Math.max(Math.abs(a), Math.abs(b));
const copysign = (m, s) => (s < 0 || Object.is(s, -0) ? -Math.abs(m) : Math.abs(m));

export class SafetyLayer {
  constructor(params = PARAMS, task = TASK) {
    this.params = params;
    this.task = task;
    this.mapper = new ControlMapper();
    this.estopLatched = false;
    this.lastCommand = null;
    this.lastCommandTime = null;
    this.prevOut = noAxes();
  }
  receive(cmd, now) {
    if (!this.mapper.accept(cmd)) return false;
    if (cmd.estop) this.estopLatched = true;
    this.lastCommand = cmd;
    this.lastCommandTime = now;
    return true;
  }
  triggerEstop() {
    this.estopLatched = true;
  }
  resetEstop() {
    this.estopLatched = false;
    this.lastCommand = null;
    this.lastCommandTime = null;
  }
  evaluate(now, gt, dt) {
    const s = this.params.safety;
    const rules = [];
    let level = 0;
    const flag = (rule, lvl) => { rules.push(rule); level = Math.max(level, lvl); };
    if (this.estopLatched) {
      flag("estop", 3);
      return this.out(noAxes(), level, rules, gt);
    }
    let want;
    if (this.lastCommand === null) want = noAxes();
    else if (now - (this.lastCommandTime ?? 0.0) > s.command_timeout_s) {
      flag("command_timeout", 2);
      want = noAxes();
    } else {
      want = {};
      for (const a of AXES) want[a] = this.lastCommand[a] ?? 0.0;
    }
    const requested = { ...want };

    const maxStep = s.max_axis_rate_per_s * dt;
    for (const a of AXES) {
      const prev = this.prevOut[a], w = want[a];
      if (Math.abs(w) > Math.abs(prev) || w * prev < 0) {
        let limited = prev + Math.max(-maxStep, Math.min(maxStep, w - prev));
        if (w * prev < 0) limited = Math.abs(prev) <= maxStep ? 0.0 : prev - copysign(maxStep, prev);
        if (!isClose(limited, w)) {
          if (!rules.includes("rate_limited")) flag("rate_limited", 0);
          want[a] = limited;
        }
      }
    }
    for (const [axis, j] of Object.entries(AXIS_TO_JOINT)) {
      const jp = this.params.joints[j];
      if (jp.type !== "revolute") continue;
      const q = gt.joints[j].position;
      if ((want[axis] > 0 && q >= jp.upper_rad - s.limit_margin_rad) || (want[axis] < 0 && q <= jp.lower_rad + s.limit_margin_rad)) {
        want[axis] = 0.0;
        if (!rules.includes("joint_limit")) flag("joint_limit", 0);
      }
    }
    const tilt = Math.max(Math.abs(gt.roll) / s.max_roll_rad, Math.abs(gt.pitch) / s.max_pitch_rad);
    if (tilt >= 1.0) {
      flag("tipover_risk", 2);
      want.swing = 0.0;
      for (const axis of WORK_AXES) if (want[axis] * this.momentGradient(gt, axis) > 0) want[axis] = 0.0;
    } else if (tilt >= 0.8) {
      flag("tipover_caution", 1);
    }
    const dNow = gt.worker_distance;
    if (dNow < s.worker_min_distance_m * 1.5) flag("worker_proximity", dNow < s.worker_min_distance_m ? 2 : 1);
    if (dNow < s.worker_min_distance_m) {
      for (const axis of ["left_track", "right_track", "swing", ...WORK_AXES]) {
        if (want[axis] !== 0.0 && this.workerDistanceAfter(gt, requested, axis) < dNow) {
          want[axis] = 0.0;
          if (!rules.includes("worker_block")) flag("worker_block", 2);
        }
      }
    }
    if (want.left_track || want.right_track) {
      const [bx, by] = this.predictBase(gt, requested);
      for (const z of this.task.forbidden_zones) {
        const dAfter = rectDistance(z, bx, by) - gt.footprint_radius;
        const dCur = rectDistance(z, gt.base_x, gt.base_y) - gt.footprint_radius;
        if (dAfter <= 0 && dAfter < dCur) {
          want.left_track = want.right_track = 0.0;
          flag("forbidden_zone", 2);
          break;
        }
      }
    }
    return this.out(want, level, rules, gt);
  }
  out(axes, level, rules, gt) {
    this.prevOut = { ...axes };
    return { command: actuator(axes), level, rules, estop: this.estopLatched, worker_distance: gt.worker_distance };
  }
  qOf(gt) {
    const q = {};
    for (const [j, t] of Object.entries(gt.joints)) q[j] = t.position;
    return q;
  }
  momentGradient(gt, axis) {
    const q = this.qOf(gt);
    const j = AXIS_TO_JOINT[axis];
    const m = (qq) => frontMoment(this.params, qq.boom_joint, qq.arm_joint, qq.bucket_joint, gt.payload_mass);
    const q2 = { ...q };
    q2[j] += PROBE;
    return m(q2) - m(q);
  }
  predictBase(gt, want) {
    const b = this.params.body;
    const v = ((want.left_track + want.right_track) / 2) * b.max_track_speed_mps;
    return [gt.base_x + v * Math.cos(gt.yaw) * PREDICT_S, gt.base_y + v * Math.sin(gt.yaw) * PREDICT_S];
  }
  workerDistanceAfter(gt, want, axis) {
    const w = this.task.worker;
    const q = this.qOf(gt);
    let bx = gt.base_x, by = gt.base_y;
    if (axis === "left_track" || axis === "right_track") {
      const single = noAxes();
      single[axis] = want[axis];
      [bx, by] = this.predictBase(gt, single);
    } else {
      const j = AXIS_TO_JOINT[axis];
      q[j] += want[axis] * this.params.joints[j].max_velocity_rps * PREDICT_S;
    }
    const fp = frontPose(this.params, q.boom_joint, q.arm_joint, q.bucket_joint);
    const [cx, cy] = toWorld(fp.bucket_center, bx, by, gt.yaw, q.swing_joint);
    const dBody = hypot(w.x - bx, w.y - by) - gt.footprint_radius;
    return Math.min(dBody, hypot(w.x - cx, w.y - cy));
  }
}

// ------------------------------------------------------------------ scripted operator
// operator.py — plays the driver for the "자동 조종" mode (the same policy that produced
// the normal training data).
const CARRY_POSE = { boom_joint: 0.75, arm_joint: -1.7 };
const BUCKET_APPROACH = 0.3;
const BUCKET_CARRY = 1.35;
const BUCKET_RELEASE = -0.3;
const JOINT_TOL = 0.03;
const SWING_TOL = 0.03;
const RELEASE_HEIGHT = 1.4;
const GAIN = 3.0;

export class ScriptedOperator {
  constructor(params = PARAMS, task = TASK, seed = 0, config = {}) {
    this.p = params;
    this.task = task;
    this.cfg = { speed_scale: 1.0, loop: false, jitter_m: 0.15, dwell_s: [0.2, 0.8], ...config };
    this.rng = new Rng(seed + 7919);
    this.sequence = 0;
    this.state = "drive";
    this.targetId = null;
    this.targetXyz = null;
    this.waitUntil = 0.0;
    this.source = task.zone_a;
    this.dest = task.zone_b;
    this.slot = 0;
    this.retries = 0;
  }
  jointAxis(gt, joint, target) {
    const jp = this.p.joints[joint];
    let err = target - gt.joints[joint].position;
    if (joint === "swing_joint") err = wrapAngle(err);
    return clip(((GAIN * err) / Math.max(jp.max_velocity_rps, 1e-6)) * jp.max_velocity_rps);
  }
  at(gt, targets) {
    for (const [j, q] of Object.entries(targets)) {
      let err = q - gt.joints[j].position;
      if (j === "swing_joint") err = wrapAngle(err);
      if (Math.abs(err) > (j === "swing_joint" ? SWING_TOL : JOINT_TOL)) return false;
    }
    return true;
  }
  swingTo(gt, x, y) {
    return wrapAngle(Math.atan2(y - gt.base_y, x - gt.base_x) - gt.yaw);
  }
  reach(gt, x, y) {
    return hypot(x - gt.base_x, y - gt.base_y);
  }
  slotXy(zone) {
    const k = this.slot % 3;
    const ang = Math.PI / 2 + (k * 2 * Math.PI) / 3;
    const j = this.cfg.jitter_m;
    return [zone.x + 1.0 * Math.cos(ang) + this.rng.uniform(-j, j), zone.y + 1.0 * Math.sin(ang) + this.rng.uniform(-j, j)];
  }
  nextObject(gt) {
    const c = gt.objects.filter((o) => o.state !== "attached" && inCircle(this.source, o.x, o.y));
    if (!c.length) return null;
    let best = c[0], bd = hypot(best.x - gt.base_x, best.y - gt.base_y);
    for (const o of c.slice(1)) {
      const d = hypot(o.x - gt.base_x, o.y - gt.base_y);
      if (d < bd) { best = o; bd = d; }
    }
    return best;
  }
  cmd(t, axes = {}) {
    this.sequence += 1;
    const s = this.cfg.speed_scale;
    const c = { timestamp: t, device: "script", sequence: this.sequence, estop: false };
    for (const a of AXES) c[a] = a in axes ? clip(axes[a] * s) : 0.0;
    return c;
  }
  dwell(t) {
    const [lo, hi] = this.cfg.dwell_s;
    this.waitUntil = t + this.rng.uniform(lo, hi);
  }
  act(gt) {
    const t = gt.sim_time;
    if (t < this.waitUntil) return this.cmd(t);
    return this["s_" + this.state](gt, t);
  }
  s_drive(gt, t) {
    const wp = this.task.work_position;
    const dx = wp.x - gt.base_x, dy = wp.y - gt.base_y;
    const dist = hypot(dx, dy);
    if (dist < 0.15) {
      this.state = "pick_raise";
      this.dwell(t);
      return this.cmd(t);
    }
    const headingErr = wrapAngle(Math.atan2(dy, dx) - gt.yaw);
    const steer = clip(-2.0 * headingErr);
    const drive = Math.abs(headingErr) < 0.3 ? clip(1.2 * dist) : 0.0;
    return this.cmd(t, { left_track: clip(drive + steer), right_track: clip(drive - steer) });
  }
  s_pick_raise(gt, t) {
    const obj = this.nextObject(gt);
    if (obj === null) {
      if (this.cfg.loop) {
        [this.source, this.dest] = [this.dest, this.source];
        return this.cmd(t);
      }
      this.state = "done";
      return this.cmd(t);
    }
    this.targetId = obj.object_id;
    const j = this.cfg.jitter_m * 0.3;
    this.targetXyz = [obj.x + this.rng.uniform(-j, j), obj.y + this.rng.uniform(-j, j), obj.z];
    const target = { ...CARRY_POSE, swing_joint: this.swingTo(gt, obj.x, obj.y) };
    if (this.at(gt, target)) {
      this.state = "pick_lower";
      return this.cmd(t);
    }
    return this.move(gt, t, target, BUCKET_APPROACH);
  }
  s_pick_lower(gt, t) {
    const [x, y, z] = this.targetXyz;
    const sol = bucketCenterIk(this.p, this.reach(gt, x, y), z, this.p.grasp.close_threshold_rad);
    if (sol === null) {
      this.state = "pick_raise";
      return this.cmd(t);
    }
    const target = { swing_joint: this.swingTo(gt, x, y), boom_joint: sol[0], arm_joint: sol[1] };
    if (this.at(gt, target)) {
      this.state = "pick_close";
      return this.cmd(t);
    }
    return this.move(gt, t, target, BUCKET_APPROACH);
  }
  s_pick_close(gt, t) {
    if (gt.payload_id !== null) {
      this.state = "carry_raise";
      this.retries = 0;
      this.dwell(t);
      return this.cmd(t);
    }
    if (gt.joints.bucket_joint.position >= BUCKET_CARRY - JOINT_TOL) {
      this.retries += 1;
      this.state = "pick_reopen";
      return this.cmd(t);
    }
    return this.cmd(t, { bucket: 1.0 });
  }
  s_pick_reopen(gt, t) {
    if (gt.joints.bucket_joint.position <= BUCKET_APPROACH + JOINT_TOL) {
      this.state = this.retries < 3 ? "pick_raise" : "pick_skip";
      return this.cmd(t);
    }
    return this.cmd(t, { bucket: -1.0, boom: 0.3 });
  }
  s_pick_skip(gt, t) {
    this.state = "done";
    return this.cmd(t);
  }
  s_carry_raise(gt, t) {
    const target = { ...CARRY_POSE };
    if (this.at(gt, target)) {
      this.state = "carry_swing";
      this.targetXyz = [...this.slotXy(this.dest), RELEASE_HEIGHT];
      return this.cmd(t);
    }
    return this.move(gt, t, target, BUCKET_CARRY);
  }
  s_carry_swing(gt, t) {
    const [x, y] = this.targetXyz;
    const target = { ...CARRY_POSE, swing_joint: this.swingTo(gt, x, y) };
    if (this.at(gt, target)) {
      this.state = "carry_lower";
      return this.cmd(t);
    }
    return this.move(gt, t, target, BUCKET_CARRY);
  }
  s_carry_lower(gt, t) {
    const [x, y, z] = this.targetXyz;
    const sol = bucketCenterIk(this.p, this.reach(gt, x, y), z, BUCKET_CARRY);
    if (sol === null) {
      this.state = "release";
      return this.cmd(t);
    }
    const target = { swing_joint: this.swingTo(gt, x, y), boom_joint: sol[0], arm_joint: sol[1] };
    if (this.at(gt, target)) {
      this.state = "release";
      return this.cmd(t);
    }
    return this.move(gt, t, target, BUCKET_CARRY);
  }
  s_release(gt, t) {
    if (gt.payload_id === null && gt.joints.bucket_joint.position <= BUCKET_RELEASE + 0.1) {
      this.slot += 1;
      this.state = "pick_raise";
      this.dwell(t);
      return this.cmd(t);
    }
    return this.cmd(t, { bucket: -1.0 });
  }
  s_done(gt, t) {
    return this.cmd(t);
  }
  move(gt, t, target, bucket) {
    const axes = { bucket: this.jointAxis(gt, "bucket_joint", bucket) };
    const names = { swing_joint: "swing", boom_joint: "boom", arm_joint: "arm" };
    const low = gt.bucket_center[2] < 1.0 && "swing_joint" in target && !("boom_joint" in target);
    for (const [j, q] of Object.entries(target)) {
      if (j === "swing_joint" && low) continue;
      axes[names[j]] = this.jointAxis(gt, j, q);
    }
    return this.cmd(t, axes);
  }
  get done() {
    return this.state === "done";
  }
}

// ------------------------------------------------------------------ session
// session.py: operator command -> Safety Layer -> simulator, one fixed step at a time.
export class SimSession {
  constructor({ seed = 0, faults = [], params = PARAMS, task = TASK, payloadScale = 1.0 } = {}) {
    this.params = params;
    this.task = task;
    this.faults = new FaultInjector(faults, seed);
    this.sim = new Simulator({ seed, params, task, faults: this.faults, payloadScale });
    this.safety = new SafetyLayer(params, task);
    this.seed = seed;
    this.gt = this.sim.groundTruth();
    this.lastRules = "";
    this.lastDecision = null;
  }
  get simTime() {
    return this.sim.t;
  }
  inject(f) {
    this.faults.add(f);
  }
  submit(cmd) {
    return this.safety.receive(cmd, this.simTime);
  }
  step() {
    const decision = this.safety.evaluate(this.simTime, this.gt, DT);
    const samples = this.sim.step(decision.command);
    this.gt = this.sim.groundTruth();
    const safetyEvents = [];
    const notable = decision.rules.filter((r) => r !== "rate_limited");
    const key = notable.join(",");
    if (key !== this.lastRules && notable.length && decision.level >= 1) {
      safetyEvents.push({ time: this.simTime, level: RISK[decision.level], rules: notable });
    }
    this.lastRules = key;
    this.lastDecision = decision;
    return { samples, decision, events: this.gt.events, safetyEvents };
  }
}
