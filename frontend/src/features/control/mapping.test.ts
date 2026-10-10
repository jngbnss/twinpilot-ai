import { gamepadToCommand, isActive, keyboardToCommand } from "./mapping";

const btn = (pressed = false, value = 0) => ({ pressed, value });

describe("keyboard mapping (mirrors backend mapper)", () => {
  it("maps drive, boom and bucket", () => {
    const c = keyboardToCommand(["w", "r", "h"], 1, 0);
    expect([c.left_track, c.right_track, c.boom, c.bucket]).toEqual([1, 1, 1, 1]);
    expect(c.device).toBe("keyboard");
  });
  it("turns right in place with D", () => {
    const c = keyboardToCommand(["d"], 2, 0);
    expect(c.left_track).toBe(1);
    expect(c.right_track).toBe(-1);
  });
  it("is case-insensitive and maps swing / bucket open", () => {
    const c = keyboardToCommand(["Q", "y"], 3, 0);
    expect(c.swing).toBe(1);
    expect(c.bucket).toBe(-1);
  });
  it("space is e-stop", () => {
    expect(keyboardToCommand([" "], 4, 0).estop).toBe(true);
  });
  it("no keys -> inactive zero command", () => {
    const c = keyboardToCommand([], 5, 0);
    expect(isActive(c)).toBe(false);
  });
});

describe("gamepad mapping", () => {
  it("applies deadzone and standard layout", () => {
    const buttons = Array.from({ length: 12 }, () => btn());
    buttons[7] = btn(true, 0.8);
    buttons[4] = btn(true, 1);
    const c = gamepadToCommand({ axes: [0.05, -1, 0, -0.5], buttons }, 1, 0);
    expect(c.left_track).toBe(1);
    expect(c.right_track).toBe(1);
    expect(c.boom).toBe(0.5);
    expect(c.bucket).toBe(0.8);
    expect(c.swing).toBe(1);
    expect(c.estop).toBe(false);
  });
});
