import { useEffect, useRef, useState } from "react";
import type { ControlCommand } from "../../types/api";
import { ESTOP_KEY, KEY_BINDINGS, gamepadToCommand, isActive, keyboardToCommand } from "./mapping";

const RATE_HZ = 20;
const RELEASE_TAIL_MS = 300; // keep sending zeros briefly after release so the machine stops crisply

export interface ControlSink {
  send: (cmd: ControlCommand) => void;
  estop: () => void;
}

/**
 * Keyboard + gamepad input -> ControlCommand at 20 Hz.
 * Only sends while the operator is giving input (so an autopilot run is not taken over
 * by idle zeros). Losing window focus clears all keys; the backend's command timeout
 * then stops the machine.
 */
export function useControlLoop(enabled: boolean, sink: ControlSink) {
  const pressed = useRef(new Set<string>());
  // wall-clock based start keeps sequences increasing across page reloads within one run
  const seq = useRef(Date.now());
  const lastActive = useRef(Number.NEGATIVE_INFINITY);
  const [keys, setKeys] = useState<string[]>([]);
  const [device, setDevice] = useState<"keyboard" | "gamepad" | null>(null);
  const sinkRef = useRef(sink);
  sinkRef.current = sink;

  useEffect(() => {
    const isEditable = (el: EventTarget | null) =>
      el instanceof HTMLElement && ["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName);
    const down = (e: KeyboardEvent) => {
      if (isEditable(e.target)) return;
      const k = e.key === " " ? ESTOP_KEY : e.key.toLowerCase();
      if (k === ESTOP_KEY) {
        e.preventDefault();
        sinkRef.current.estop();
      }
      if (KEY_BINDINGS[k] || k === ESTOP_KEY) {
        pressed.current.add(k);
        setKeys([...pressed.current]);
      }
    };
    const up = (e: KeyboardEvent) => {
      const k = e.key === " " ? ESTOP_KEY : e.key.toLowerCase();
      pressed.current.delete(k);
      setKeys([...pressed.current]);
    };
    const blur = () => {
      pressed.current.clear();
      setKeys([]);
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => {
      const now = performance.now();
      const ts = (now / 1000) % 1e6;
      let cmd = keyboardToCommand(pressed.current, seq.current + 1, ts);
      let dev: "keyboard" | "gamepad" = "keyboard";
      const pad = typeof navigator.getGamepads === "function" ? navigator.getGamepads()[0] : null;
      if (!isActive(cmd) && pad) {
        const pc = gamepadToCommand(pad, seq.current + 1, ts);
        if (isActive(pc)) {
          cmd = pc;
          dev = "gamepad";
          if (pc.estop) sinkRef.current.estop();
        }
      }
      const active = isActive(cmd);
      if (active) lastActive.current = now;
      if (active || now - lastActive.current < RELEASE_TAIL_MS) {
        seq.current += 1;
        sinkRef.current.send(cmd);
        setDevice(dev);
      }
    }, 1000 / RATE_HZ);
    return () => clearInterval(id);
  }, [enabled]);

  return { keys, device };
}
