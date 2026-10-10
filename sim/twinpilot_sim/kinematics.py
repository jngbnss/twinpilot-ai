"""Planar forward/inverse kinematics of the excavator front attachment.

Conventions (upper-body vertical plane, r forward from swing center, z up):
- boom angle: from horizontal, positive up
- arm angle: relative to boom, negative folds down
- bucket angle: relative to arm, positive curls (closes) the bucket
- bucket world angle phi = boom + arm - bucket
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from twinpilot_sim.params import ExcavatorParams


@dataclass(frozen=True)
class Point2:
    r: float
    z: float


@dataclass(frozen=True)
class FrontPose:
    pivot: Point2
    boom_tip: Point2
    arm_tip: Point2
    bucket_center: Point2
    bucket_tip: Point2
    phi: float


def front_pose(p: ExcavatorParams, boom: float, arm: float, bucket: float) -> FrontPose:
    lb, la, lk = p.links.boom.length_m, p.links.arm.length_m, p.links.bucket.length_m
    p0 = Point2(p.boom_pivot.x_m, p.boom_pivot.z_m)
    p1 = Point2(p0.r + lb * math.cos(boom), p0.z + lb * math.sin(boom))
    a = boom + arm
    p2 = Point2(p1.r + la * math.cos(a), p1.z + la * math.sin(a))
    phi = a - bucket
    bc = Point2(p2.r + 0.5 * lk * math.cos(phi), p2.z + 0.5 * lk * math.sin(phi))
    p3 = Point2(p2.r + lk * math.cos(phi), p2.z + lk * math.sin(phi))
    return FrontPose(p0, p1, p2, bc, p3, phi)


def front_moment(
    p: ExcavatorParams, boom: float, arm: float, bucket: float, payload: float
) -> float:
    """Horizontal moment (kg*m) of the front attachment about the swing center."""
    fp = front_pose(p, boom, arm, bucket)
    lk = p.links
    return (
        lk.boom.mass_kg * (fp.pivot.r + fp.boom_tip.r) / 2
        + lk.arm.mass_kg * (fp.boom_tip.r + fp.arm_tip.r) / 2
        + (lk.bucket.mass_kg + payload) * fp.bucket_center.r
    )


def to_world(
    pt: Point2, base_x: float, base_y: float, yaw: float, swing: float
) -> tuple[float, float, float]:
    heading = yaw + swing
    return (base_x + pt.r * math.cos(heading), base_y + pt.r * math.sin(heading), pt.z)


def arm_ik(p: ExcavatorParams, r: float, z: float) -> tuple[float, float] | None:
    """Boom/arm angles that put the arm tip at (r, z). Elbow folded down. None if unreachable."""
    lb, la = p.links.boom.length_m, p.links.arm.length_m
    dx, dz = r - p.boom_pivot.x_m, z - p.boom_pivot.z_m
    d = (dx * dx + dz * dz - lb * lb - la * la) / (2 * lb * la)
    if not -1.0 <= d <= 1.0:
        return None
    arm = -math.acos(d)
    boom = math.atan2(dz, dx) - math.atan2(la * math.sin(arm), lb + la * math.cos(arm))
    return boom, arm


def bucket_center_ik(
    p: ExcavatorParams, r: float, z: float, bucket: float, iterations: int = 4
) -> tuple[float, float] | None:
    """Boom/arm angles that put the bucket center at (r, z) for a given bucket angle."""
    half = 0.5 * p.links.bucket.length_m
    sol = arm_ik(p, r, z + half)
    for _ in range(iterations):
        if sol is None:
            return None
        phi = sol[0] + sol[1] - bucket
        sol = arm_ik(p, r - half * math.cos(phi), z - half * math.sin(phi))
    return sol


def wrap_angle(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi
