// TwinPilot AI web demo — UI controller. Everything runs in this browser tab (Level 1).
import { SimSession, ScriptedOperator, keyboardToCommand, KEY_BINDINGS, PARAMS, TASK, DT, RISK, faultActive } from "./sim.js";
import { loadBundle, FAST_CHANNELS, SLOW_CHANNELS } from "./detect.js";
import { Monitor } from "./monitor.js";
import { drawChart } from "./charts.js";

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// ------------------------------------------------------------------ static info
const DET_INFO = {
  threshold: { name: "규칙 기준선", kind: "고정 임계값 · 정상 p0.5–p99.5 한계", color: "#e8c547" },
  zscore: { name: "Z-score", kind: "통계 기준선 · 최대 |z|", color: "#8fa3ff" },
  "zscore-tail": { name: "Tail-normalized Z", kind: "통계 기준선 · 저장소 운영 기본", color: "#4fd1c5" },
  "isolation-forest": { name: "Isolation Forest", kind: "학습 모델 · 트리 200개", color: "#f06fb4" },
};
const DET_ORDER = ["threshold", "isolation-forest", "zscore-tail", "zscore"];

// sim/twinpilot_sim/schema.py FaultType + targets from FaultPanel.tsx / make_dataset.py;
// default severity/duration from simulation/fault-scenarios/*.yaml
const FAULTS = {
  JOINT_LAG: { name: "관절 응답 지연", targets: ["bucket_joint", "arm_joint", "boom_joint", "swing_joint"], dur: 15, desc: "관절 시정수 × (1 + 4·심각도)" },
  FRICTION_INCREASE: { name: "마찰 증가", targets: ["boom_joint", "arm_joint", "bucket_joint", "swing_joint"], dur: 20, desc: "점성 마찰 × (1 + 3·심각도)" },
  SENSOR_NOISE: { name: "센서 노이즈", targets: ["arm_joint.effort", "boom_joint.velocity", "imu.roll", "chassis.vibration_rms", "bucket_joint.position"], dur: 15, desc: "측정값에 가우시안 노이즈" },
  SENSOR_STUCK: { name: "센서 고착", targets: ["swing_joint.position", "boom_joint.position", "imu.pitch", "bucket_joint.velocity"], dur: 15, desc: "측정값이 한 값에 멈춤" },
  TEMP_RISE: { name: "유압유 온도 상승", targets: ["hydraulic"], dur: 25, desc: "유압유 가열 +0.5·심각도 °C/s" },
};
const RULES_KO = {
  estop: "비상 정지", command_timeout: "명령 타임아웃", rate_limited: "변화율 제한", joint_limit: "관절 한계",
  tipover_risk: "전복 위험 · 동작 제한", tipover_caution: "전복 주의", worker_proximity: "작업자 근접",
  worker_block: "작업자 방향 동작 차단", forbidden_zone: "금지 구역 진입 차단",
};
const RULE_LEVEL = { estop: 3, command_timeout: 2, rate_limited: 0, joint_limit: 0, tipover_risk: 2, tipover_caution: 1, worker_proximity: 1, worker_block: 2, forbidden_zone: 2 };
const RISK_KO = ["안전 · 정상", "안전 · 주의", "안전 · 경고", "안전 · 비상"];
const WORLD_KO = {
  attach: (d) => `운반물 집음 (${d.object_id})`, release: (d) => `운반물 내려놓음 (${d.object_id})`,
  delivered: (d) => `구역 B 운반 완료 (${d.object_id})`, task_complete: () => "작업 완료: 3개 모두 운반",
  drop: (d) => `운반물 낙하/구역 밖 내려놓음 (${d.object_id})`, collision: () => "차체가 운반물과 충돌",
  worker_collision: () => "작업자 충돌", worker_proximity: (d) => `작업자 최소 거리 침범 (${d.distance?.toFixed(1)} m)`,
  forbidden_zone_entry: () => "금지 구역 진입", tipover_risk: () => "전복 위험 자세",
};
const JOINT_KO = { swing_joint: "스윙", boom_joint: "붐", arm_joint: "암", bucket_joint: "버킷" };
const MESSAGE = "에서 평소와 다른 패턴이 감지되었습니다. 원인 진단은 별도 확인이 필요합니다.";

// ------------------------------------------------------------------ state
const S = {
  bundle: null, scene: null, session: null, op: null, monitor: null,
  mode: "auto", speed: 1, seed: 0, seq: 0, pressed: new Set(), lastManualT: -1e9, cycles: 0, doneAt: null,
  hist: { fast: {}, slow: {} }, scores: [], log: [], logFilter: "all", prScope: "auto",
  faultSel: "JOINT_LAG", faultN: 0, endedFaults: new Set(), dirty: true,
};
const HIST_S = 32;

// a replacement operator keeps the sequence counter: the Control Mapper rejects
// non-increasing sequence numbers per device (control/mapper.py)
function makeOperator(seed) {
  const op = new ScriptedOperator(PARAMS, TASK, seed, { loop: true });
  if (S.op) op.sequence = S.op.sequence;
  return op;
}

function newRun(seed = Math.floor(Math.random() * 1e6)) {
  S.seed = seed;
  S.session = new SimSession({ seed });
  S.op = null;
  S.op = makeOperator(seed);
  S.monitor = new Monitor(S.bundle);
  S.monitor.setFaults(S.session.faults.faults); // shared array: labels = injected faults
  S.seq = 0;
  S.cycles = 1;
  S.doneAt = null;
  S.hist = { fast: {}, slow: {} };
  S.scores = [];
  S.log = [];
  S.endedFaults = new Set();
  S.lastManualT = -1e9;
  addLog("world", `새 실행 시작 · seed ${seed} · ${S.mode === "auto" ? "자동 조종" : "수동 조종"}`);
  renderFaultList();
}

function addLog(kind, html, t = S.session?.simTime ?? 0) {
  S.log.push({ kind, html, t });
  if (S.log.length > 400) S.log.splice(0, S.log.length - 400);
  S.logDirty = true;
}

// ------------------------------------------------------------------ simulation loop
function setMode(mode, why) {
  if (S.mode === mode) return;
  S.mode = mode;
  if (mode === "auto") {
    // fresh scripted operator: it reads the current ground truth and resumes the task
    S.op = makeOperator(S.seed + S.cycles * 31 + Math.floor(S.session.simTime));
    S.pressed.clear();
    syncPad();
  }
  addLog("world", mode === "auto" ? "자동 조종 재개" : `수동 조종으로 전환${why ? ` (${why})` : ""}`);
  if (why) toast("수동 조종으로 전환했습니다. ‘자동 조종’으로 되돌릴 수 있습니다.");
  renderMode();
}

function stepOnce() {
  const s = S.session;
  if (S.mode === "auto") {
    s.submit(S.op.act(s.gt));
  } else {
    s.submit(keyboardToCommand(S.pressed, ++S.seq, s.simTime));
    S.lastManualT = s.simTime;
  }
  const st = s.step();
  const t = s.simTime;
  S.monitor.mode = t - S.lastManualT <= 2.0 ? "manual" : "auto";
  recordSamples(t, st.samples);
  const events = S.monitor.push(t, st.samples);
  if (S.monitor.latest && S.monitor.latest.t === t) {
    const r = S.monitor.latest.results;
    S.scores.push({ t, r: Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v.score / v.threshold])) });
    if (S.scores.length > 200) S.scores.splice(0, S.scores.length - 200);
  }
  for (const e of events) {
    const info = DET_INFO[e.detector];
    const dot = `<i class="det-dot" style="background:${info.color}"></i>`;
    if (e.kind === "anomaly") {
      addLog("anomaly", `${dot}<span class="who">${info.name}</span> 경보 · 점수 ${fmtScore(e.score)} &gt; ${fmtScore(e.threshold)}<br><span class="mono">${esc(e.sensors.join(", "))}</span>`, t);
    } else {
      addLog("cleared", `${dot}<span class="who">${info.name}</span> 경보 해제`, t);
    }
  }
  for (const e of st.safetyEvents) {
    addLog("safety", `안전 계층 <b>${e.level}</b> · ${e.rules.map((r) => RULES_KO[r] ?? r).join(", ")}`, t);
  }
  for (const e of st.events) {
    const f = WORLD_KO[e.type];
    addLog("world", f ? f(e.detail ?? {}) : e.type, t);
  }
  for (const f of s.faults.faults) {
    if (!S.endedFaults.has(f.fault_id) && t >= f.start_time + f.duration) {
      S.endedFaults.add(f.fault_id);
      addLog("fault", `고장 종료 · ${FAULTS[f.type].name} → <span class="mono">${esc(f.target)}</span>`, t);
      renderFaultList();
    }
  }
  // keep the autopilot busy: after a finished cycle put the objects back in zone A
  if (S.mode === "auto" && S.op.done) {
    S.doneAt ??= t;
    if (t - S.doneAt > 1.5) {
      s.sim.world.reset();
      s.gt = s.sim.groundTruth();
      S.cycles += 1;
      S.doneAt = null;
      S.op = makeOperator(S.seed + S.cycles * 31);
      addLog("world", `운반물 재배치 → 작업 사이클 ${S.cycles} 시작`, t);
    }
  }
}

function recordSamples(t, samples) {
  for (const [kind, block] of [["fast", samples.fast], ["slow", samples.slow]]) {
    if (!block) continue;
    const h = S.hist[kind];
    for (const [k, v] of Object.entries(block)) {
      const arr = (h[k] ??= []);
      arr.push([t, v]);
      if (arr.length > 1100) arr.splice(0, 200);
    }
  }
}

let acc = 0, lastFrame = performance.now(), lastUi = 0, lastChart = 0;
function frame(now) {
  const dt = Math.min(0.1, (now - lastFrame) / 1000);
  lastFrame = now;
  if (S.session && !document.hidden) {
    acc += dt * S.speed;
    let n = 0;
    while (acc >= DT && n < 40) {
      stepOnce();
      acc -= DT;
      n++;
    }
    if (n === 40) acc = 0;
  }
  if (S.session) {
    const gt = S.session.gt;
    const d = S.session.lastDecision;
    if (S.scene) S.scene.update(gt, d && d.rules.includes("worker_proximity"));
    if (now - lastUi > 120) { lastUi = now; renderLive(); }
    if (now - lastChart > 100) { lastChart = now; renderCharts(); }
  }
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------ rendering
const fmtScore = (v) => (Math.abs(v) >= 1e4 ? v.toExponential(1) : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(3));
const pct = (v) => (v === null || v === undefined ? "–" : (v * 100).toFixed(1) + "%");
const num = (v, d = 3) => (v === null || v === undefined ? "–" : v.toFixed(d));

function renderMode() {
  $("#btn-auto").classList.toggle("on", S.mode === "auto");
  $("#btn-manual").classList.toggle("on", S.mode === "manual");
  $("#hud-mode").textContent = S.mode === "auto" ? "자동 조종" : "수동 조종";
  const note = $("#mode-note");
  if (S.mode === "auto") {
    note.className = "mode-note";
    note.innerHTML = "<b>자동 조종</b> — 저장소의 스크립트 조종기가 운반 작업을 반복합니다. 탐지 모델이 학습한 정상 데이터와 같은 조건이라 고장 주입 실험에 알맞습니다. 조종 버튼이나 키를 누르면 수동으로 전환됩니다.";
  } else {
    note.className = "mode-note warn";
    note.innerHTML = "<b>수동 조종</b> — W/S 주행 · A/D 선회 · Q/E 스윙 · R/F 붐 · T/G 암 · Y/H 버킷 · Space 비상 정지. 탐지 모델은 자동 조종 데이터로만 학습되어 수동 조종(특히 주행·급조작)에서는 <b>오경보가 크게 늘어납니다</b>. P/R의 ‘자동 조종 구간’ 집계에서는 수동 구간을 뺍니다.";
  }
}

function renderLive() {
  const s = S.session, gt = s.gt, d = s.lastDecision;
  $("#hud-time").textContent = `t = ${s.simTime.toFixed(1)} s`;
  $("#hud-time").title = `seed ${S.seed}`;
  const c = s.sim.world.counters;
  $("#hud-task").textContent = `운반 ${c.delivered} / ${TASK.target_count}${S.cycles > 1 ? ` · 사이클 ${S.cycles}` : ""}`;
  const level = d ? d.level : 0;
  const risk = $("#hud-risk");
  risk.dataset.level = level;
  risk.textContent = RISK_KO[level] + (d && d.rules.length ? ` · ${d.rules.filter((r) => r !== "rate_limited").map((r) => RULES_KO[r]).join(", ") || "변화율 제한"}` : "");
  const latched = s.safety.estopLatched;
  $("#btn-estop").classList.toggle("latched", latched);
  $("#btn-estop-reset").hidden = !latched;

  // detectors
  const latest = S.monitor.latest;
  const html = DET_ORDER.map((k) => {
    const info = DET_INFO[k], det = S.bundle.detectors[k];
    const r = latest?.results[k];
    const ratio = r ? r.score / r.threshold : 0;
    const w = Math.min(100, (ratio / 3) * 100);
    const state = !r ? ["대기", ""] : r.alarm ? ["경보", "alarm"] : r.is_anomaly ? ["이상 창", "anom"] : ["정상", ""];
    const primary = k === S.bundle.primary ? " · primary" : "";
    const sensors = r?.is_anomaly && r.sensors.length ? r.sensors : null;
    return `<div class="det ${r?.alarm ? "alarm" : ""}">
      <div class="det-top"><span class="det-name"><i style="background:${info.color}"></i>${info.name}</span><span class="det-state ${state[1]}">${state[0]}</span></div>
      <div class="det-kind">${info.kind}${primary}</div>
      <div class="bar"><span style="width:${w}%;background:${info.color}"></span></div>
      <div class="det-meta"><span class="mono">점수 ${r ? fmtScore(r.score) : "–"}</span><span class="mono">임계값 ${fmtScore(det.threshold)}</span></div>
      ${sensors ? `<div class="det-sensors">${esc(sensors.join(", "))}</div>` : ""}
    </div>`;
  }).join("");
  $("#detectors").innerHTML = html;

  // alarm banner (primary detector, like the backend service message)
  const alarmed = DET_ORDER.filter((k) => latest?.results[k]?.alarm);
  const banner = $("#hud-alarm");
  if (alarmed.length) {
    const p = latest.results[S.bundle.primary];
    const sens = p.alarm && latest.sensors.length ? latest.sensors.join(", ") : null;
    banner.hidden = false;
    banner.innerHTML = `<b>이상 경보</b> · ${alarmed.map((k) => DET_INFO[k].name).join(", ")}${sens ? `<br><span class="mono">${esc(sens)}</span>${MESSAGE}` : ""}`;
  } else banner.hidden = true;

  // safety
  const rules = d ? d.rules : [];
  const tilt = Math.max(Math.abs(gt.roll) / PARAMS.safety.max_roll_rad, Math.abs(gt.pitch) / PARAMS.safety.max_pitch_rad);
  $("#safety").innerHTML = `
    <span class="k">위험 수준</span><span>${RISK[level]} (${RISK_KO[level].split(" · ")[1]})</span>
    <span class="k">명령 출처</span><span>${S.mode === "auto" ? "스크립트 조종기" : "키보드 / 화면 버튼"}</span>
    <span class="k">작업자 거리</span><span class="mono">${gt.worker_distance.toFixed(1)} m (최소 ${PARAMS.safety.worker_min_distance_m} m)</span>
    <span class="k">전복 지표</span><span class="mono">${(tilt * 100).toFixed(0)}% (100% = 한계)</span>
    <span class="k">유압유</span><span class="mono">${s.sim.temp.toFixed(1)} °C</span>
    <span class="k">비상 정지</span><span>${latched ? "<b style='color:var(--danger)'>래치됨</b>" : "해제"}</span>
    <div class="rules">${rules.length ? rules.map((r) => `<span class="rule" data-level="${RULE_LEVEL[r] ?? 0}">${RULES_KO[r] ?? r}</span>`).join("") : '<span class="rule">적용 중인 규칙 없음</span>'}</div>`;

  renderFaultTimers();
  if (S.logDirty) renderLog();
  renderPR();
}

function renderFaultList() {
  const ul = $("#active-faults");
  const fs = S.session ? S.session.faults.faults : [];
  ul.innerHTML = fs.slice().reverse().map((f) => {
    const done = S.endedFaults.has(f.fault_id);
    return `<li class="${done ? "done" : ""}" data-id="${f.fault_id}">
      <span><b>${FAULTS[f.type].name}</b> · <span class="mono">${esc(f.target)}</span> · 심각도 ${f.severity.toFixed(1)}<br>
      <span class="mono" data-timer>${done ? `종료 (${f.start_time.toFixed(1)}–${(f.start_time + f.duration).toFixed(1)} s)` : ""}</span></span>
      ${done ? "" : '<button type="button" class="ghost sm" data-stop>중지</button>'}</li>`;
  }).join("");
}

function renderFaultTimers() {
  const t = S.session.simTime;
  for (const f of S.session.faults.faults) {
    if (S.endedFaults.has(f.fault_id)) continue;
    const el = document.querySelector(`#active-faults li[data-id="${f.fault_id}"] [data-timer]`);
    if (el) el.textContent = faultActive(f, t) ? `진행 중 · 남은 ${(f.start_time + f.duration - t).toFixed(1)} s` : "대기";
  }
}

function renderLog() {
  S.logDirty = false;
  const f = S.logFilter;
  const items = S.log.filter((e) => f === "all" || (f === "anomaly" ? e.kind === "anomaly" || e.kind === "cleared" || e.kind === "fault" : e.kind === "safety"));
  const html = items.slice(-150).reverse().map((e) => `<li class="${e.kind}"><time>${e.t.toFixed(1)}s</time><div>${e.html}</div></li>`).join("");
  $("#log").innerHTML = html || '<li class="empty">아직 기록이 없습니다.</li>';
}

function renderPR() {
  const m = S.monitor.metrics(S.prScope);
  const delays = S.monitor.delays();
  const anyPos = Object.values(m).some((c) => c.tp + c.fn > 0);
  const n = Object.values(m)[0];
  const total = n.tp + n.fp + n.fn + n.tn;
  const bestF1 = Math.max(...Object.values(m).map((c) => c.f1 ?? -1));
  const rows = DET_ORDER.map((k) => {
    const c = m[k], info = DET_INFO[k];
    const ds = delays[k].map((d) => (d.detected ? `${d.delay_s}s` : "미탐")).join(" · ") || "–";
    return `<tr><td><span class="sw" style="background:${info.color}"></span>${info.name}</td>
      <td>${pct(c.precision)}</td><td>${pct(c.recall)}</td><td class="${c.f1 !== null && c.f1 === bestF1 && bestF1 > 0 ? "best" : ""}">${num(c.f1)}</td>
      <td>${pct(c.fpr)}</td><td class="mono">${c.tp}/${c.fp}/${c.fn}/${c.tn}</td><td>${ds}</td></tr>`;
  }).join("");
  const bars = DET_ORDER.map((k) => {
    const c = m[k], info = DET_INFO[k];
    return `<div class="prrow"><span class="lbl">${info.name}</span><div class="prtrack">
      <div class="p" title="Precision"><span style="width:${(c.precision ?? 0) * 100}%;background:${info.color}"></span></div>
      <div class="r" title="Recall"><span style="width:${(c.recall ?? 0) * 100}%;background:${info.color}"></span></div></div></div>`;
  }).join("");
  const head = anyPos
    ? `<div class="prbars">${bars}</div><p class="fine" style="margin-top:0">막대: 위 = Precision, 아래(옅게) = Recall</p>`
    : `<p class="pr-empty">고장을 주입하면 재현율·정밀도가 계산됩니다. 지금은 정상 구간의 오경보율(FPR)만 의미가 있습니다. (집계 창 ${total}개)</p>`;
  const html = `${head}<div class="tbl-wrap"><table><thead><tr><th>탐지기</th><th>Precision</th><th>Recall</th><th>F1</th><th>FPR</th><th>TP/FP/FN/TN</th><th>탐지 지연</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  if (html !== S.prHtml) { S.prHtml = html; $("#pr").innerHTML = html; }
}

function renderOffline() {
  const off = S.bundle.offline_eval;
  if (!off) { $("#offline").innerHTML = '<p class="pr-empty">오프라인 평가 결과가 모델 파일에 없습니다.</p>'; return; }
  const best = Math.max(...Object.values(off.detectors).map((d) => d.f1));
  const rows = DET_ORDER.map((k) => {
    const d = off.detectors[k], info = DET_INFO[k];
    return `<tr><td><span class="sw" style="background:${info.color}"></span>${info.name}</td><td>${pct(d.precision)}</td><td>${pct(d.recall)}</td>
      <td class="${d.f1 === best ? "best" : ""}">${num(d.f1)}</td><td>${pct(d.fpr)}</td><td>${pct(d.run_detection_rate)}</td>
      <td>${d.median_delay_s ?? "–"}${d.median_delay_s != null ? " s" : ""}</td><td>${d.false_alarms_per_hour}</td></tr>`;
  }).join("");
  $("#offline").innerHTML = `<div class="tbl-wrap"><table><thead><tr><th>탐지기</th><th>Precision</th><th>Recall</th><th>F1</th><th>FPR</th><th>run 탐지율</th><th>지연 중앙값</th><th>오경보/h</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  const c = S.bundle.calibration;
  $("#offline-note").innerHTML = `이 페이지가 쓰는 JSON 모델과 같은 번들을 저장소 <code>evaluation/metrics.py</code>로 dataset v1 test split(${off.runs} runs, 고장 5종 × 심각도 0.3/0.6/0.9 + 정상, 자동 조종)에 적용한 결과입니다. 학습: 정상 창 ${c.n_train_windows}개, 임계값: val 정상 창 ${c.n_val_normal_windows}개에서 FPR ${c.target_fpr * 100}%로 보정. 특징 세트 <code>${S.bundle.feature_set}</code>(${S.bundle.feature_names.length}개). Isolation Forest는 저장소 실험에서도 이 설정으로 거의 탐지하지 못했습니다(<code>docs/experiments.md</code>).`;
}

function renderCharts() {
  const t1 = Math.max(S.session.simTime, HIST_S - 2), t0 = t1 - 30;
  const bands = S.session.faults.faults.map((f) => [f.start_time, f.start_time + f.duration]);
  const scoreSeries = DET_ORDER.map((k) => ({ color: DET_INFO[k].color, width: k === S.bundle.primary ? 2 : 1.4, data: S.scores.map((p) => [p.t, Math.min(p.r[k], 3.2)]) }));
  drawChart($("#c-score"), { series: scoreSeries, t0, t1, fixedMin: 0, fixedMax: 3.2, bands, hline: { v: 1, label: "임계값" }, yFmt: (v) => (v >= 3 ? "3+" : v.toFixed(1)) });
  const j = $("#chart-joint").value;
  const H = S.hist.fast, Hs = S.hist.slow;
  drawChart($("#c-vel"), {
    series: [
      { color: "#6b7d8a", width: 1.4, dash: [4, 3], data: H[`${j}.command`] ?? [] },
      { color: "#f2a541", width: 1.6, data: H[`${j}.velocity`] ?? [] },
    ], t0, t1, bands, minSpan: 0.1,
  });
  drawChart($("#c-eff"), { series: [{ color: "#8fa3ff", data: (H[`${j}.effort`] ?? []).map(([t, v]) => [t, v / 1000]) }], t0, t1, bands, minSpan: 1 });
  drawChart($("#c-temp"), { series: [{ color: "#ef5b5b", data: Hs["hydraulic.oil_temperature"] ?? [] }], t0, t1, bands, minSpan: 1 });
  const ch = $("#chart-channel").value;
  drawChart($("#c-chan"), { series: [{ color: "#4fd1c5", data: H[ch] ?? Hs[ch] ?? [] }], t0, t1, bands, minSpan: 1e-3 });
}

// ------------------------------------------------------------------ faults UI
function buildFaultUi() {
  $("#fault-presets").innerHTML = Object.entries(FAULTS).map(([k, f]) =>
    `<button type="button" class="fault-btn ${k === S.faultSel ? "sel" : ""}" data-type="${k}"><b>${f.name}</b><span>${k}</span></button>`).join("");
  selectFault(S.faultSel);
  $("#fault-presets").addEventListener("click", (e) => {
    const b = e.target.closest(".fault-btn");
    if (b) selectFault(b.dataset.type);
  });
  $("#fault-sev").addEventListener("input", (e) => ($("#sev-val").textContent = Number(e.target.value).toFixed(1)));
  $("#fault-dur").addEventListener("input", (e) => ($("#dur-val").textContent = `${e.target.value} s`));
  $("#btn-inject").addEventListener("click", injectFault);
  $("#active-faults").addEventListener("click", (e) => {
    if (!e.target.closest("[data-stop]")) return;
    const id = e.target.closest("li").dataset.id;
    const f = S.session.faults.faults.find((x) => x.fault_id === id);
    const t = S.session.simTime;
    if (f) f.duration = Math.max(0.02, t - f.start_time); // ends now; the label follows
  });
}

function selectFault(type) {
  S.faultSel = type;
  $$(".fault-btn").forEach((b) => b.classList.toggle("sel", b.dataset.type === type));
  const f = FAULTS[type];
  $("#fault-target").innerHTML = f.targets.map((t) => `<option>${t}</option>`).join("");
  $("#fault-dur").value = f.dur;
  $("#dur-val").textContent = `${f.dur} s`;
  if (type === "SENSOR_STUCK") { $("#fault-sev").value = 1; $("#sev-val").textContent = "1.0"; }
  $("#btn-inject").textContent = `${f.name} 주입 — ${f.desc}`;
}

function injectFault() {
  const type = S.faultSel, target = $("#fault-target").value;
  const f = {
    fault_id: `fault-${String(++S.faultN).padStart(3, "0")}`, type, target,
    start_time: Math.round(S.session.simTime * 100) / 100,
    duration: Number($("#fault-dur").value), severity: Number($("#fault-sev").value),
  };
  S.session.inject(f);
  addLog("fault", `고장 주입 · <b>${FAULTS[type].name}</b> → <span class="mono">${esc(target)}</span> · 심각도 ${f.severity.toFixed(1)} · ${f.duration} s`);
  if (target in JOINT_KO) $("#chart-joint").value = target;
  else if (target.includes(".")) {
    $("#chart-channel").value = target;
    const j = target.split(".")[0];
    if (j in JOINT_KO) $("#chart-joint").value = j;
  } else if (target === "hydraulic") $("#chart-channel").value = "hydraulic.oil_temperature";
  updateCaptions();
  renderFaultList();
  if (S.mode === "manual") toast("수동 조종 중에는 오경보가 섞입니다. 자동 조종에서 비교하면 더 정확합니다.");
  if (["JOINT_LAG", "FRICTION_INCREASE"].includes(type) && S.mode === "manual") toast("관절 고장은 해당 관절을 움직여야 센서에 드러납니다.");
}

function updateCaptions() {
  const j = JOINT_KO[$("#chart-joint").value];
  $("#cap-vel").innerHTML = `${j} 속도 <span style="color:#f2a541">측정</span> vs <span style="color:#93a4b1">명령</span> (rad/s)`;
  $("#cap-eff").textContent = `${j} 토크 effort (kN·m)`;
}

// ------------------------------------------------------------------ input
function syncPad() {
  $$(".pad button").forEach((b) => b.classList.toggle("held", S.pressed.has(b.dataset.key)));
}
function press(key) {
  if (!S.session) return;
  if (S.mode === "auto") setMode("manual", "조종 입력");
  S.pressed.add(key);
  syncPad();
}
function release(key) {
  S.pressed.delete(key);
  syncPad();
}
function estop() {
  S.session.safety.triggerEstop();
  addLog("safety", "<b>비상 정지</b> 입력 — 모든 축 정지, 해제 전까지 래치");
}

function bindInput() {
  const editable = (el) => el instanceof HTMLElement && ["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName);
  window.addEventListener("keydown", (e) => {
    if (editable(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === " ") {
      e.preventDefault();
      if (!e.repeat) estop();
      return;
    }
    const k = e.key.toLowerCase();
    if (KEY_BINDINGS[k]) {
      e.preventDefault();
      press(k);
    }
  });
  window.addEventListener("keyup", (e) => release(e.key.toLowerCase()));
  window.addEventListener("blur", () => { S.pressed.clear(); syncPad(); });
  for (const b of $$(".pad button")) {
    const k = b.dataset.key;
    b.addEventListener("pointerdown", (e) => { e.preventDefault(); b.setPointerCapture?.(e.pointerId); press(k); });
    for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) b.addEventListener(ev, () => release(k));
    b.addEventListener("contextmenu", (e) => e.preventDefault());
  }
  $("#btn-auto").addEventListener("click", () => setMode("auto"));
  $("#btn-manual").addEventListener("click", () => setMode("manual"));
  $$("[data-speed]").forEach((b) => b.addEventListener("click", () => {
    S.speed = Number(b.dataset.speed);
    $$("[data-speed]").forEach((x) => x.classList.toggle("on", x === b));
  }));
  $("#btn-reset").addEventListener("click", () => { newRun(); S.prHtml = ""; });
  $("#btn-estop").addEventListener("click", estop);
  $("#btn-estop-reset").addEventListener("click", () => {
    S.session.safety.resetEstop();
    addLog("safety", "비상 정지 해제 (운영자 명시 조작)");
  });
  $("#btn-view").addEventListener("click", () => {
    if (!S.scene) return;
    const v = S.scene.view === "orbit" ? "cab" : "orbit";
    S.scene.setView(v);
    $("#btn-view").textContent = v === "orbit" ? "운전석 시점" : "궤도 시점";
  });
  $$("[data-log]").forEach((b) => b.addEventListener("click", () => {
    S.logFilter = b.dataset.log;
    $$("[data-log]").forEach((x) => x.classList.toggle("on", x === b));
    renderLog();
  }));
  $$("[data-scope]").forEach((b) => b.addEventListener("click", () => {
    S.prScope = b.dataset.scope;
    $$("[data-scope]").forEach((x) => x.classList.toggle("on", x === b));
    renderPR();
  }));
  $("#chart-joint").addEventListener("change", updateCaptions);
}

let toastTimer = null;
function toast(msg) {
  let el = $(".toast");
  if (!el) { el = document.createElement("div"); el.className = "toast"; el.setAttribute("role", "status"); document.body.append(el); }
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3500);
}

// ------------------------------------------------------------------ boot
async function boot() {
  const legend = DET_ORDER.map((k) => `<span><i style="background:${DET_INFO[k].color}"></i>${DET_INFO[k].name}</span>`).join("");
  $("#legend-score").innerHTML = legend;
  const chans = [...FAST_CHANNELS.filter((c) => !c.endsWith(".command")), ...SLOW_CHANNELS];
  $("#chart-channel").innerHTML = chans.map((c) => `<option ${c === "imu.roll" ? "selected" : ""}>${c}</option>`).join("");
  updateCaptions();
  try {
    const res = await fetch("model/detector-v1-residual.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    S.bundle = loadBundle(await res.json());
  } catch (err) {
    $("#loading").textContent = `탐지 모델을 불러오지 못했습니다 (${err.message}). 페이지를 새로고침해 주세요.`;
    return;
  }
  buildFaultUi();
  bindInput();
  newRun();
  renderMode();
  renderOffline();
  try {
    const { Scene } = await import("./scene.js");
    S.scene = new Scene($("#viewport"), PARAMS, TASK);
    $("#loading").remove();
  } catch (err) {
    console.error(err);
    $("#loading").textContent = "3D 장면을 불러오지 못했습니다(네트워크 또는 WebGL). 시뮬레이션·탐지·그래프는 계속 동작합니다.";
  }
  requestAnimationFrame(frame);
}

boot();
