import pytest
from pydantic import ValidationError

from twinpilot_sim.params import default_params
from twinpilot_sim.schema import (
    JOINTS,
    ControlCommand,
    FaultSpec,
    FaultType,
    Quality,
    TelemetryRecord,
)
from twinpilot_sim.units import Unit


def record(**kw):
    base = dict(
        run_id="run-1",
        timestamp_ns=0,
        simulation_time=14.25,
        source="boom_joint",
        metric="effort",
        value=73.4,
        unit="Nm",
        quality="simulated",
        sequence=481,
    )
    base.update(kw)
    return TelemetryRecord(**base)


def fault(**kw):
    base = dict(
        fault_id="f1",
        type="JOINT_LAG",
        target="bucket_joint",
        start_time=42,
        duration=15,
        severity=0.6,
    )
    base.update(kw)
    return FaultSpec(**base)


def test_valid_record():
    r = record()
    assert r.unit is Unit.NEWTON_METER
    assert r.quality is Quality.SIMULATED
    assert r.key == "boom_joint.effort"


@pytest.mark.parametrize("unit", ["", None, "furlong"])
def test_rejects_missing_or_unknown_unit(unit):
    with pytest.raises(ValidationError):
        record(unit=unit)


def test_rejects_missing_unit_field():
    data = record().model_dump()
    del data["unit"]
    with pytest.raises(ValidationError):
        TelemetryRecord(**data)


def test_rejects_bad_quality():
    with pytest.raises(ValidationError):
        record(quality="guessed")


def test_rejects_non_finite_value():
    with pytest.raises(ValidationError):
        record(value=float("nan"))


def test_rejects_bad_source_name():
    with pytest.raises(ValidationError):
        record(source="Boom Joint")


def test_record_is_immutable():
    r = record()
    with pytest.raises(ValidationError):
        r.value = 1.0


def test_control_axes_bounded():
    with pytest.raises(ValidationError):
        ControlCommand(timestamp=0, device="keyboard", sequence=0, boom=1.5)
    c = ControlCommand(timestamp=0, device="gamepad", sequence=1, boom=0.43)
    assert c.axes()["boom"] == 0.43


def test_fault_target_validation():
    fault()
    with pytest.raises(ValidationError):
        fault(target="imu.roll")
    with pytest.raises(ValidationError):
        fault(type=FaultType.SENSOR_STUCK, target="boom_joint")
    with pytest.raises(ValidationError):
        fault(type=FaultType.TEMP_RISE, target="boom_joint")
    with pytest.raises(ValidationError):
        fault(severity=1.5)


def test_fault_active_window():
    f = fault(type="TEMP_RISE", target="hydraulic", start_time=10, duration=5)
    assert not f.active(9.99)
    assert f.active(10)
    assert not f.active(15)


def test_params_load_and_hash_stable():
    p = default_params()
    assert set(p.joints) == set(JOINTS)
    assert p.joints["boom_joint"].lower_rad == -0.5
    assert p.joints["boom_joint"].upper_rad == 1.2
    assert len(p.params_hash()) == 16
    assert p.params_hash() == default_params().params_hash()
