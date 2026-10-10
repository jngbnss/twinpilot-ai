# Architecture

전체 데이터 흐름과 디렉토리 설계는 [`implementation-plan.md`](implementation-plan.md) 1절 참조.

## Phase 1 — `sim/twinpilot_sim`

| 모듈 | 역할 |
|------|------|
| `schema.py`, `units.py` | TelemetryRecord / ControlCommand / FaultSpec / RunMetadata, 허용 단위 |
| `params.py`, `params/excavator.yaml` | 물리 파라미터 단일 소스 + 해시 |
| `kinematics.py` | 정기구학, IK, 전면부 모멘트 |
| `world.py`, `scenarios/excavator_carry.yaml` | 작업 구역·운반물·작업자·금지 구역, attach/detach, 작업 카운터 |
| `adapter.py` | `SimulatorAdapter`, `ActuatorCommand`, `GroundTruth`, `FaultModel` 인터페이스 |
| `simulator.py` | `PythonSimulator` — 50 Hz 물리, 25/5 Hz 센서, seed 결정론 |
| `control/mapper.py` | 키보드·게임패드 → ControlCommand, sequence 검증 |
| `control/safety.py` | 결정론 Safety Layer (E-stop, 타임아웃, 변화율, 관절 한계, 전복, 작업자 거리, 금지 구역) |
| `faults.py` | FaultInjector (5종), 별도 RNG 스트림 |
| `operator.py` | Scripted operator 상태머신 |
| `session.py` | 명령 → Safety → 시뮬레이터 스텝, 안전 이벤트, 작업 지표 |
| `recorder.py`, `runner.py`, `cli.py` | JSONL/Parquet 기록, 배치 실행, CLI |

스텝 순서: `submit(cmd)` → `SafetyLayer.evaluate(now, ground_truth)` → `PythonSimulator.step(ActuatorCommand)` → TelemetryRecord[] + WorldEvent[].
