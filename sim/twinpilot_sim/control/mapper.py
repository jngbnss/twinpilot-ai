"""Control Mapper: raw keyboard / gamepad input -> validated ControlCommand.

Operator input never reaches the simulator directly; it goes through this mapper and the
Control Safety Layer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from twinpilot_sim.schema import ControlCommand

# key -> (axis, direction). "steer" is converted to differential track speeds.
KEY_BINDINGS: dict[str, tuple[str, float]] = {
    "w": ("drive", 1.0),
    "s": ("drive", -1.0),
    "a": ("steer", -1.0),
    "d": ("steer", 1.0),
    "q": ("swing", 1.0),
    "e": ("swing", -1.0),
    "r": ("boom", 1.0),
    "f": ("boom", -1.0),
    "t": ("arm", 1.0),
    "g": ("arm", -1.0),
    "y": ("bucket", -1.0),  # open
    "h": ("bucket", 1.0),  # close (curl)
}
ESTOP_KEY = " "
DEADZONE = 0.1


def _clip(v: float) -> float:
    return max(-1.0, min(1.0, v))


def _dz(v: float) -> float:
    return 0.0 if abs(v) < DEADZONE else v


def tracks_from(drive: float, steer: float) -> tuple[float, float]:
    """Differential drive mix. steer > 0 turns right (clockwise)."""
    return _clip(drive + steer), _clip(drive - steer)


def keyboard_to_command(
    pressed: Iterable[str], sequence: int, timestamp: float, scale: float = 1.0
) -> ControlCommand:
    acc = {"drive": 0.0, "steer": 0.0, "swing": 0.0, "boom": 0.0, "arm": 0.0, "bucket": 0.0}
    keys = {k.lower() if k != ESTOP_KEY else k for k in pressed}
    for k in keys:
        if k in KEY_BINDINGS:
            axis, sign = KEY_BINDINGS[k]
            acc[axis] += sign
    left, right = tracks_from(acc["drive"] * scale, acc["steer"] * scale)
    return ControlCommand(
        timestamp=timestamp,
        device="keyboard",
        sequence=sequence,
        left_track=left,
        right_track=right,
        swing=_clip(acc["swing"] * scale),
        boom=_clip(acc["boom"] * scale),
        arm=_clip(acc["arm"] * scale),
        bucket=_clip(acc["bucket"] * scale),
        estop=ESTOP_KEY in keys,
    )


def gamepad_to_command(
    axes: Mapping[str, float], buttons: Mapping[str, bool], sequence: int, timestamp: float
) -> ControlCommand:
    """Left stick drive, right stick boom/arm, triggers bucket, shoulders swing."""
    drive = -_dz(axes.get("left_y", 0.0))  # stick up is negative in the Gamepad API
    steer = _dz(axes.get("left_x", 0.0))
    left, right = tracks_from(drive, steer)
    swing = (1.0 if buttons.get("lb") else 0.0) - (1.0 if buttons.get("rb") else 0.0)
    bucket = _dz(axes.get("rt", 0.0)) - _dz(axes.get("lt", 0.0))
    return ControlCommand(
        timestamp=timestamp,
        device="gamepad",
        sequence=sequence,
        left_track=left,
        right_track=right,
        swing=swing,
        boom=_clip(-_dz(axes.get("right_y", 0.0))),
        arm=_clip(_dz(axes.get("right_x", 0.0))),
        bucket=_clip(bucket),
        estop=bool(buttons.get("estop")),
    )


@dataclass
class ControlMapper:
    """Validates sequence ordering per device and keeps the latest accepted command."""

    last_sequence: dict[str, int] = field(default_factory=dict)
    rejected: int = 0
    accepted: int = 0

    def accept(self, cmd: ControlCommand) -> bool:
        prev = self.last_sequence.get(cmd.device)
        if prev is not None and cmd.sequence <= prev:
            self.rejected += 1
            return False
        self.last_sequence[cmd.device] = cmd.sequence
        self.accepted += 1
        return True
