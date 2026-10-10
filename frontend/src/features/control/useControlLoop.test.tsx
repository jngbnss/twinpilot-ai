import { act, renderHook } from "@testing-library/react";
import type { ControlCommand } from "../../types/api";
import { useControlLoop } from "./useControlLoop";

describe("useControlLoop", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("sends while a key is held and stops after focus loss", () => {
    const sent: ControlCommand[] = [];
    const sink = { send: (c: ControlCommand) => sent.push(c), estop: vi.fn() };
    renderHook(() => useControlLoop(true, sink));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "r" }));
      vi.advanceTimersByTime(200);
    });
    expect(sent.length).toBeGreaterThan(0);
    expect(sent[sent.length - 1].boom).toBe(1);
    act(() => {
      window.dispatchEvent(new Event("blur"));
      vi.advanceTimersByTime(1000);
    });
    const after = sent.length;
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(sent.length).toBe(after); // nothing sent after focus loss -> backend timeout stops
    expect(sent[sent.length - 1].boom).toBe(0);
  });

  it("does not send idle zeros (autopilot is not taken over)", () => {
    const sent: ControlCommand[] = [];
    renderHook(() => useControlLoop(true, { send: (c) => sent.push(c), estop: vi.fn() }));
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(sent).toHaveLength(0);
  });

  it("space triggers e-stop immediately", () => {
    const estop = vi.fn();
    renderHook(() => useControlLoop(false, { send: vi.fn(), estop }));
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: " " }));
    });
    expect(estop).toHaveBeenCalledTimes(1);
  });
});
