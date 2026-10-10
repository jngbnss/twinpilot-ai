"""Allowed measurement units. Every telemetry value must carry one of these."""

from enum import StrEnum


class Unit(StrEnum):
    RAD = "rad"
    RAD_PER_S = "rad/s"
    NEWTON_METER = "Nm"
    METER = "m"
    METER_PER_S = "m/s"
    METER_PER_S2 = "m/s^2"
    DEG_C = "degC"
    PERCENT = "percent"
    KILOJOULE = "kJ"
    KILOGRAM = "kg"
    SECOND = "s"
    BOOL = "bool"
    COUNT = "count"
