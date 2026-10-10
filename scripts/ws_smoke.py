"""End-to-end smoke test against a running backend.

uv run python -m uvicorn app.main:app --app-dir backend --port 8770
uv run python scripts/ws_smoke.py [--base http://127.0.0.1:8770]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx
import websockets


async def ws_session(ws_url: str, seconds: float, send_control: bool) -> list[dict]:
    snaps = []
    async with websockets.connect(ws_url) as ws:
        seq = 0
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if send_control:
                seq += 1
                await ws.send(
                    json.dumps(
                        {
                            "type": "control",
                            "data": {
                                "timestamp": time.time() % 1e6,
                                "device": "keyboard",
                                "sequence": seq,
                                "boom": 1.0,
                                "swing": 0.5,
                            },
                        }
                    )
                )
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.05))
                if msg["type"] == "twin_state":
                    snaps.append(msg["data"])
            except TimeoutError:
                pass
    return snaps


def check(cond: bool, label: str) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8770")
    a = ap.parse_args()
    ws_url = a.base.replace("http", "ws") + "/ws/twin"
    c = httpx.Client(base_url=a.base, timeout=30)

    check(c.get("/api/health").json()["status"] == "ok", "health")
    run_id = c.post("/api/runs", json={"mode": "live", "seed": 1}).json()["run_id"]
    print("live run:", run_id)
    snaps = asyncio.run(ws_session(ws_url, 2.0, send_control=True))
    check(len(snaps) >= 10, f"ws snapshots received: {len(snaps)} (10 Hz)")
    boom = snaps[-1]["joint_states"]["boom_joint"]["position"]
    check(boom > 0.65, f"boom moved by WS control: {boom:.3f} rad")
    print("sync:", snaps[-1]["sync"])

    f = c.post(
        "/api/faults",
        json={"type": "JOINT_LAG", "target": "bucket_joint", "severity": 0.8, "duration": 10},
    ).json()
    check(f["kind"] == "fault", f"fault injected at t={f['start_time']}")
    time.sleep(1.0)
    st = c.get("/api/twin/state").json()
    check("command_timeout" in st["safety_state"]["rules"], "timeout stop after control stopped")

    report = c.post("/api/runs/stop").json()["report"]
    check(report["run_id"] == run_id, "report generated")
    print("score:", report["score"])
    print("raw:", {k: report["raw_metrics"][k] for k in ("sim_time", "delivered", "safety_events")})

    c.post("/api/runs", json={"mode": "replay", "run_id": run_id})
    c.post("/api/replay/speed", json={"speed": 4})
    c.post("/api/replay/play")
    snaps = asyncio.run(ws_session(ws_url, 1.0, send_control=False))
    check(bool(snaps) and snaps[-1]["mode"] == "replay", "replay streaming")
    q = {s["quality"] for s in snaps[-1]["sensor_states"].values()}
    check(q == {"replayed"}, f"replay quality {q}")
    print("replay cursor:", snaps[-1]["replay"])
    c.post("/api/runs/stop")
    print("SMOKE OK")


if __name__ == "__main__":
    main()
