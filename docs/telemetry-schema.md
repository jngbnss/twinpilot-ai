# Telemetry Schema

구현: `sim/twinpilot_sim/schema.py`, 단위: `sim/twinpilot_sim/units.py`.

## TelemetryRecord

| 필드 | 타입 | 설명 |
|------|------|------|
| run_id | str | 시뮬레이션 run ID |
| timestamp_ns | int ≥ 0 | 레코드 생성 wall-clock 시간 (ns). 수신 시간은 backend가 별도 기록 |
| simulation_time | float ≥ 0 | simulation time (s) |
| source | `^[a-z][a-z0-9_]*$` | 센서 ID (`boom_joint`, `imu`, `hydraulic` …) |
| metric | 같은 패턴 | 측정 항목 (`position`, `effort` …) |
| value | float (유한값) | |
| unit | `Unit` enum (필수) | 단위 없는 수치 저장 금지 |
| quality | simulated / replayed / real | 데이터 품질 |
| sequence | int ≥ 0 | source별 단조 증가 |

예:

```json
{"run_id": "run-20261011-001", "timestamp_ns": 1760140800000000000, "simulation_time": 14.25,
 "source": "boom_joint", "metric": "effort", "value": 73.4, "unit": "Nm",
 "quality": "simulated", "sequence": 481}
```

## 허용 단위

`rad`, `rad/s`, `Nm`, `m`, `m/s`, `m/s^2`, `degC`, `percent`, `kJ`, `kg`, `s`, `bool`, `count`

## ControlCommand

전체 축 스냅샷 (D-010). 축 범위 [-1, 1]: `left_track`, `right_track`, `swing`, `boom`, `arm`, `bucket`.
`estop`, `device` (keyboard/gamepad/script/api), `sequence`, `timestamp`.

## FaultSpec

`fault_id`, `type` (JOINT_LAG / FRICTION_INCREASE / SENSOR_NOISE / SENSOR_STUCK / TEMP_RISE), `target`,
`start_time`, `duration`, `severity ∈ [0, 1]`. 관절 고장은 관절 ID, 센서 고장은 `source.metric`,
TEMP_RISE는 `hydraulic`. 평가용 정답 라벨로 run 메타에 저장.

## RunMetadata

`run_id`, `created_at`, `level`, `sim_version`, `seed`, `scenario`, `params_hash`, `dt`, `duration`,
`faults`, `operator`, `result`.
