// Minimal canvas line charts (no dependencies). Time on x (seconds), auto or fixed y range.

export function setupCanvas(canvas) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const r = canvas.getBoundingClientRect();
  const w = Math.max(10, Math.round(r.width * dpr)), h = Math.max(10, Math.round(r.height * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: r.width, h: r.height };
}

function niceStep(range, target) {
  const raw = range / Math.max(1, target);
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / p;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
}

const fmt = (v, step) => {
  const d = Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
  return v.toFixed(Math.min(d, 4));
};

/**
 * opts: { series: [{data: [[t, v], ...], color, width?, dash?}], t0, t1,
 *         yMin?, yMax?, clampMax?, bands: [[a, b]], hline?: {v, label}, yFmt? }
 */
export function drawChart(canvas, opts) {
  const { ctx, w, h } = setupCanvas(canvas);
  const L = 44, R = 8, T = 6, B = 18;
  const pw = w - L - R, ph = h - T - B;
  ctx.clearRect(0, 0, w, h);
  const { t0, t1 } = opts;
  let lo = Infinity, hi = -Infinity;
  for (const s of opts.series) for (const [t, v] of s.data) if (t >= t0) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (opts.yMin !== undefined) lo = Math.min(lo, opts.yMin);
  if (opts.yMax !== undefined) hi = Math.max(hi, opts.yMax);
  if (opts.fixedMin !== undefined) lo = opts.fixedMin;
  if (opts.fixedMax !== undefined) hi = opts.fixedMax;
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) { lo = 0; hi = 1; }
  if (hi - lo < (opts.minSpan ?? 1e-6)) { const c = (hi + lo) / 2, s = (opts.minSpan ?? 1e-3) / 2; lo = c - s; hi = c + s; }
  const pad = (hi - lo) * 0.08;
  if (opts.fixedMin === undefined) lo -= pad;
  if (opts.fixedMax === undefined) hi += pad;
  const X = (t) => L + ((t - t0) / (t1 - t0)) * pw;
  const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * ph;

  // fault bands
  ctx.fillStyle = "rgba(242,165,65,0.13)";
  for (const [a, b] of opts.bands ?? []) {
    const xa = Math.max(L, X(a)), xb = Math.min(L + pw, X(b));
    if (xb > xa) ctx.fillRect(xa, T, xb - xa, ph);
  }
  // grid
  ctx.strokeStyle = "#1e2a32";
  ctx.fillStyle = "#6b7d8a";
  ctx.lineWidth = 1;
  ctx.font = "10px ui-monospace, Consolas, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  const ys = niceStep(hi - lo, 4);
  for (let v = Math.ceil(lo / ys) * ys; v <= hi + 1e-12; v += ys) {
    const y = Math.round(Y(v)) + 0.5;
    ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + pw, y); ctx.stroke();
    ctx.fillText(opts.yFmt ? opts.yFmt(v) : fmt(v, ys), L - 5, y);
  }
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const ts = niceStep(t1 - t0, 6);
  for (let t = Math.ceil(t0 / ts) * ts; t <= t1 + 1e-9; t += ts) {
    const x = Math.round(X(t)) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, T + ph); ctx.lineTo(x, T + ph + 3); ctx.stroke();
    if (t >= 0) ctx.fillText(`${Math.round(t)}s`, x, T + ph + 4);
  }
  if (opts.hline) {
    const y = Math.round(Y(opts.hline.v)) + 0.5;
    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "#c6d1d9";
    ctx.globalAlpha = 0.7;
    ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + pw, y); ctx.stroke();
    ctx.restore();
    if (opts.hline.label) {
      ctx.fillStyle = "#c6d1d9"; ctx.textAlign = "left"; ctx.textBaseline = "bottom";
      ctx.fillText(opts.hline.label, L + 4, y - 2);
    }
  }
  // series
  ctx.save();
  ctx.beginPath(); ctx.rect(L, T, pw, ph); ctx.clip();
  ctx.lineJoin = "round";
  for (const s of opts.series) {
    ctx.strokeStyle = s.color;
    ctx.lineWidth = s.width ?? 1.5;
    ctx.setLineDash(s.dash ?? []);
    ctx.beginPath();
    let started = false;
    for (const [t, v] of s.data) {
      if (t < t0 - 1) continue;
      const x = X(t), y = Y(v);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  ctx.restore();
}
