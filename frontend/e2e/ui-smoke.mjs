// Browser smoke test with the system Edge (playwright-core, no browser download).
// Requires backend (8770) + vite (5290) running.  node e2e/ui-smoke.mjs [outDir]
import { chromium } from "playwright-core";

const BASE = process.env.UI_BASE ?? "http://127.0.0.1:5290";
const OUT = process.argv[2] ?? ".";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = (path, body) =>
  fetch(BASE + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => r.json());

const browser = await chromium.launch({ channel: "msedge", headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--disk-cache-size=1", "--disable-gpu-shader-disk-cache", "--disable-breakpad"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(`${e} @ ${String(e.stack ?? "").split(/\r?\n/).slice(1, 4).join(" <- ")}`));
const check = (cond, label) => {
  console.log((cond ? "PASS " : "FAIL ") + label);
  if (!cond) process.exitCode = 1;
};

await page.goto(BASE + "/#control");
await page.getByText("연결됨").waitFor({ timeout: 15000 });
check(true, "websocket connected");

// 1) manual run: keyboard control moves the boom
await page.getByText("수동 조종 시작").click();
await sleep(1500);
const before = (await api("/api/twin/state")).joint_states.boom_joint.position;
await page.keyboard.down("f");
await sleep(1500);
await page.keyboard.up("f");
await sleep(300);
const after = (await api("/api/twin/state")).joint_states.boom_joint.position;
check(after < before - 0.2, `keyboard F lowers boom ${before.toFixed(2)} -> ${after.toFixed(2)} rad`);
await page.screenshot({ path: `${OUT}/ui-control.png` });

// 2) autopilot + fault -> anomaly score rises on the AI tab
await page.getByText("자동 조종 시작").click();
await sleep(14000);
const f = await api("/api/faults", { type: "JOINT_LAG", target: "bucket_joint", severity: 0.9, duration: 20 });
console.log(`fault injected at t=${f.start_time}`);
await page.getByRole("button", { name: "AI" }).click();
let alarm = false;
for (let i = 0; i < 30 && !alarm; i++) {
  await sleep(500);
  alarm = (await page.locator(".kpi b.alarm-text").count()) > 0;
}
check(alarm, "AI tab shows alarm after fault");
await sleep(2500);
await page.screenshot({ path: `${OUT}/ui-ai.png` });
await page.getByRole("button", { name: "비교", exact: true }).click();
await page.locator("table").last().waitFor();
await sleep(800);
await page.screenshot({ path: `${OUT}/ui-compare.png`, fullPage: true });

// 3) status tab renders badges
await page.getByRole("button", { name: "상태" }).click();
await sleep(1200);
check((await page.getByText("SIMULATED SENSOR").count()) > 1, "status tab shows SIMULATED SENSOR badges");
await page.screenshot({ path: `${OUT}/ui-status.png` });

// 4) stop -> analysis tab -> report and replay
await page.getByRole("button", { name: "조종" }).click();
await page.getByText("종료").click();
await sleep(1500);
await page.getByRole("button", { name: "분석 · 리플레이" }).click();
await page.locator("[data-testid=run-row]").first().waitFor({ timeout: 10000 });
{
  await page.locator("[data-testid=run-row]").first().click();
  await page.getByText("성과 보고서").waitFor({ timeout: 10000 });
  check(await page.getByText("고장 → 첫 경보").isVisible(), "report shows score, raw metrics and fault->alarm time");
  await sleep(1500);
  await page.getByRole("button", { name: "리플레이 열기" }).click();
  await sleep(1000);
  await page.getByRole("button", { name: "재생", exact: true }).click();
  await sleep(4000);
  const st = await api("/api/twin/state");
  check(st.mode === "replay" && st.replay.cursor > 1, `replay playing (cursor ${st.replay?.cursor})`);
  await page.screenshot({ path: `${OUT}/ui-analysis.png`, fullPage: true });
}
check(errors.length === 0, `no page errors ${errors.slice(0, 3).join(" | ")}`);
await browser.close();
console.log(process.exitCode ? "UI SMOKE FAILED" : "UI SMOKE OK");
