// Mirrors sim/twinpilot_sim/control/mapper.py so browser input and backend agree.
import type { ControlCommand } from "../../types/api";

type Axis = "drive" | "steer" | "swing" | "boom" | "arm" | "bucket";

export const KEY_BINDINGS: Record<string, [Axis, number, string]> = {
  w: ["drive", 1, "전진"],
  s: ["drive", -1, "후진"],
  a: ["steer", -1, "좌회전"],
  d: ["steer", 1, "우회전"],
  q: ["swing", 1, "스윙 L"],
  e: ["swing", -1, "스윙 R"],
  r: ["boom", 1, "붐 ↑"],
  f: ["boom", -1, "붐 ↓"],
  t: ["arm", 1, "암 펴기"],
  g: ["arm", -1, "암 접기"],
  y: ["bucket", -1, "버킷 열기"],
  h: ["bucket", 1, "버킷 닫기"],
};
export const ESTOP_KEY = " ";
const DEADZONE = 0.1;

const clip = (v: number) => Math.max(-1, Math.min(1, v));
const dz = (v: number) => (Math.abs(v) < DEADZONE ? 0 : v);

export function tracksFrom(drive: number, steer: number): [number, number] {
  return [clip(drive + steer), clip(drive - steer)];
}

export function keyboardToCommand(
  pressed: Iterable<string>,
  sequence: number,
  timestamp: number,
): ControlCommand {
  const acc: Record<Axis, number> = { drive: 0, steer: 0, swing: 0, boom: 0, arm: 0, bucket: 0 };
  let estop = false;
  for (const raw of pressed) {
    if (raw === ESTOP_KEY) estop = true;
    const b = KEY_BINDINGS[raw.toLowerCase()];
    if (b) acc[b[0]] += b[1];
  }
  const [left, right] = tracksFrom(acc.drive, acc.steer);
  return {
    timestamp,
    device: "keyboard",
    sequence,
    left_track: left,
    right_track: right,
    swing: clip(acc.swing),
    boom: clip(acc.boom),
    arm: clip(acc.arm),
    bucket: clip(acc.bucket),
    estop,
  };
}

export interface PadInput {
  axes: readonly number[]; // [lx, ly, rx, ry]
  buttons: readonly { pressed: boolean; value: number }[];
}

/** Standard Gamepad mapping: LT=6, RT=7, LB=4, RB=5, B=1 (E-stop). */
export function gamepadToCommand(pad: PadInput, sequence: number, timestamp: number): ControlCommand {
  const ax = (i: number) => dz(pad.axes[i] ?? 0);
  const btn = (i: number) => pad.buttons[i] ?? { pressed: false, value: 0 };
  const [left, right] = tracksFrom(-ax(1), ax(0));
  return {
    timestamp,
    device: "gamepad",
    sequence,
    left_track: left,
    right_track: right,
    swing: (btn(4).pressed ? 1 : 0) - (btn(5).pressed ? 1 : 0),
    boom: clip(-ax(3)),
    arm: clip(ax(2)),
    bucket: clip(dz(btn(7).value) - dz(btn(6).value)),
    estop: btn(1).pressed,
  };
}

export function isActive(cmd: ControlCommand): boolean {
  return (
    cmd.estop ||
    [cmd.left_track, cmd.right_track, cmd.swing, cmd.boom, cmd.arm, cmd.bucket].some((v) => v !== 0)
  );
}
