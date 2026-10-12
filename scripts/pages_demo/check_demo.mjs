// Parity + smoke checks for the browser demo (pages/js) against the Python reference.
//   node scripts/pages_demo/check_demo.mjs .demo-data/fixture.json
// The fixture comes from make_fixture.py; the model JSON from export_model.py.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { SimSession, ScriptedOperator, keyboardToCommand, PARAMS, JOINTS } from "../../pages/js/sim.js";
import { residualFeatures, loadBundle, scoreAll } from "../../pages/js/detect.js";
import { Monitor } from "../../pages/js/monitor.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const fixture = JSON.parse(readFileSync(process.argv[2] ?? join(root, ".demo-data/fixture.json"), "utf8"));
const model = JSON.parse(readFileSync(join(root, "pages/model/detector-v1-residual.json"), "utf8"));
const bundle = loadBundle(model);

let failures = 0;
const check = (ok, msg) => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${msg}`);
  if (!ok) failures++;
};
const rel = (a, b) => Math.abs(a - b) / Math.max(1e-12, Math.abs(a), Math.abs(b));

// ---------------------------------------------------------------- 1. detectors
{
  let nWin = 0, maxFeat = 0, maxScore = 0, decisionMismatch = 0, explainMismatch = 0;
  const perDet = {};
  for (const c of fixture.detectors) {
    for (const w of c.windows) {
      nWin++;
      const { x } = residualFeatures(w.fast, w.slow);
      if (x.length !== w.x.length) throw new Error(`feature count ${x.length} != ${w.x.length}`);
      x.forEach((v, i) => (maxFeat = Math.max(maxFeat, Math.abs(v - w.x[i]) / Math.max(1e-9, Math.abs(w.x[i])))));
      const s = scoreAll(bundle, x);
      for (const [k, py] of Object.entries(w.scores)) {
        maxScore = Math.max(maxScore, rel(s[k].score, py));
        const pyAnom = py > model.detectors[k].threshold;
        if (pyAnom !== s[k].is_anomaly) decisionMismatch++;
        perDet[k] ??= { pos: 0 };
        if (pyAnom) perDet[k].pos++;
      }
      const ex = bundle.detectors[model.primary].explain(x);
      if (ex.join() !== w.explain.join()) explainMismatch++;
    }
  }
  check(nWin > 400, `detector fixture has ${nWin} windows from ${fixture.detectors.length} eval cases`);
  check(maxFeat < 1e-7, `83 residual features match Python (max rel err ${maxFeat.toExponential(2)})`);
  check(maxScore < 1e-9, `all 4 detector scores match Python (max rel err ${maxScore.toExponential(2)})`);
  check(decisionMismatch === 0, `anomaly decisions identical (${decisionMismatch} mismatches)`);
  check(explainMismatch === 0, `primary explain() sensors identical (${explainMismatch} mismatches)`);
  console.log("      positive windows per detector:", JSON.stringify(perDet));
}

// ---------------------------------------------------------------- 2. simulator parity
const quiet = structuredClone(PARAMS);
for (const k of Object.keys(quiet.sensors.noise)) quiet.sensors.noise[k] = 0;

function compareTrace(name, py, js) {
  check(py.length === js.length, `${name}: ${js.length} states (python ${py.length})`);
  let maxPos = 0, maxQ = 0, maxEff = 0, maxTemp = 0, discrete = 0, firstBad = null;
  for (let i = 0; i < Math.min(py.length, js.length); i++) {
    const a = py[i], b = js[i];
    maxPos = Math.max(maxPos, Math.abs(a.x - b.x), Math.abs(a.y - b.y), Math.abs(a.yaw - b.yaw));
    a.q.forEach((v, k) => (maxQ = Math.max(maxQ, Math.abs(v - b.q[k]), Math.abs(a.qd[k] - b.qd[k]))));
    a.effort.forEach((v, k) => (maxEff = Math.max(maxEff, Math.abs(v - b.effort[k]) / Math.max(1, Math.abs(v)))));
    maxTemp = Math.max(maxTemp, Math.abs(a.temp - b.temp), Math.abs(a.energy - b.energy) / Math.max(1, a.energy));
    const same = a.delivered === b.delivered && a.payload === b.payload && a.rules.join() === b.rules.join() &&
      a.level === b.level && (a.op_state === undefined || a.op_state === b.op_state);
    if (!same) {
      discrete++;
      firstBad ??= { t: a.t, py: [a.rules, a.op_state, a.payload], js: [b.rules, b.op_state, b.payload] };
    }
  }
  check(maxPos < 1e-6, `${name}: base pose matches (max err ${maxPos.toExponential(2)})`);
  check(maxQ < 1e-6, `${name}: joint position/velocity match (max err ${maxQ.toExponential(2)})`);
  check(maxEff < 1e-6, `${name}: joint effort matches (max rel err ${maxEff.toExponential(2)})`);
  check(maxTemp < 1e-6, `${name}: oil temperature / energy match (max err ${maxTemp.toExponential(2)})`);
  check(discrete === 0, `${name}: safety rules, risk level, grasp/delivery, operator state identical` +
    (firstBad ? ` — first diff ${JSON.stringify(firstBad)}` : ""));
}

function state(session, decision, op) {
  const gt = session.gt, sim = session.sim;
  const s = {
    t: session.simTime, x: gt.base_x, y: gt.base_y, yaw: gt.yaw,
    q: JOINTS.map((j) => gt.joints[j].position), qd: JOINTS.map((j) => gt.joints[j].velocity),
    effort: JOINTS.map((j) => gt.joints[j].effort), temp: sim.temp, energy: sim.energyKj,
    delivered: sim.world.counters.delivered, payload: gt.payload_id, rules: decision.rules, level: decision.level,
  };
  if (op) s.op_state = op.state;
  return s;
}

{
  const ref = fixture.sim_operator;
  const session = new SimSession({ seed: 7, faults: ref.faults, params: quiet });
  const op = new ScriptedOperator(quiet, session.task, 7, { loop: true, jitter_m: 0.0, dwell_s: [0.3, 0.3] });
  const js = [];
  while (session.simTime < 90 - 1e-9) {
    session.submit(op.act(session.gt));
    const st = session.step();
    if (session.sim.tick % 10 === 0) js.push(state(session, st.decision, op));
  }
  compareTrace("scripted operator + faults (90 s)", ref.trace, js);
  const last = ref.trace.at(-1);
  console.log(`      python run delivered ${last.delivered} objects; JS delivered ${js.at(-1).delivered}`);
}

{
  const ref = fixture.sim_keyboard;
  const session = new SimSession({ seed: 3, params: quiet });
  let seq = 0;
  const js = [];
  const rulesSeen = new Set();
  for (const [keys, dur] of ref.plan) {
    if (keys === "RESET") {
      session.safety.resetEstop();
      continue;
    }
    const n = Math.round(dur / 0.02);
    for (let i = 0; i < n; i++) {
      if (keys !== "TIMEOUT") session.submit(keyboardToCommand([...keys], ++seq, session.simTime));
      const st = session.step();
      st.decision.rules.forEach((r) => rulesSeen.add(r));
      if (session.sim.tick % 5 === 0) js.push(state(session, st.decision));
    }
  }
  compareTrace(`keyboard + safety interlocks (${(session.simTime).toFixed(0)} s)`, ref.trace, js);
  console.log("      safety rules exercised:", [...rulesSeen].join(", "));
}

// ---------------------------------------------------------------- 3. noisy end-to-end smoke
function runMonitored(seed, faults, duration) {
  const session = new SimSession({ seed });
  const op = new ScriptedOperator(PARAMS, session.task, seed, { loop: true });
  const mon = new Monitor(bundle);
  for (const f of faults) session.inject(f);
  mon.setFaults(faults);
  let nan = false;
  while (session.simTime < duration - 1e-9) {
    session.submit(op.act(session.gt));
    const st = session.step();
    mon.push(session.simTime, st.samples);
    for (const v of Object.values(st.samples.fast ?? {})) if (!Number.isFinite(v)) nan = true;
  }
  return { mon, nan, session };
}

{
  const { mon, nan } = runMonitored(11, [], 180);
  check(!nan, "JS noisy run: all telemetry finite");
  const m = mon.metrics();
  const fpr = Object.fromEntries(Object.entries(m).map(([k, v]) => [k, +(v.fp / Math.max(1, v.fp + v.tn)).toFixed(3)]));
  console.log("      normal 180 s autopilot, window FPR per detector:", JSON.stringify(fpr));
  check(fpr[model.primary] <= 0.05, `normal JS run: primary detector window FPR ${fpr[model.primary]} <= 0.05`);
}

const CASES = [
  { type: "JOINT_LAG", target: "bucket_joint", severity: 0.9, duration: 20 },
  { type: "FRICTION_INCREASE", target: "boom_joint", severity: 0.9, duration: 20 },
  { type: "SENSOR_NOISE", target: "arm_joint.effort", severity: 0.9, duration: 15 },
  { type: "SENSOR_STUCK", target: "boom_joint.position", severity: 1.0, duration: 15 },
  { type: "TEMP_RISE", target: "hydraulic", severity: 0.9, duration: 25 },
];
for (const [i, c] of CASES.entries()) {
  const f = { fault_id: `t${i}`, start_time: 20, ...c };
  const { mon } = runMonitored(500 + i, [f], 60);
  const m = mon.metrics();
  const det = Object.entries(mon.delays()).map(([k, d]) => `${k}:${d[0]?.delay_s ?? "miss"}`).join(" ");
  const p = m[model.primary];
  console.log(`      ${c.type}/${c.target} sev ${c.severity}: delays ${det}`);
  check(mon.delays()[model.primary][0]?.detected ?? false, `JS end-to-end ${c.type} detected by primary (recall ${(p.tp / Math.max(1, p.tp + p.fn)).toFixed(2)})`);
}

console.log(failures ? `\n${failures} check(s) FAILED` : "\nall checks passed");
process.exit(failures ? 1 : 0);
