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

## Phase 2 — `backend/app`

| 모듈 | 역할 |
|------|------|
| `telemetry/source.py` | `LiveSimSource`(실시간 50 Hz, autopilot 선택) / `ReplaySource`(play·pause·speed·seek, `quality=replayed`) |
| `twin/store.py` | Twin State Store — sim time 정렬, stale, 누락·역전, 지연, 120 s 히스토리, 스냅샷 |
| `engine.py` | 활성 run 1개 관리, 기록, 안전 상태, 이상 탐지 훅(읽기 전용), WS 브로드캐스트 10 Hz |
| `tasks/report.py` | 점수 + 원시 지표 리포트, fault→탐지 응답 시간 |
| `runs/index.py` | SQLite run 인덱스 (CLI run 자동 동기화) |
| `api/routes.py` | REST + `/ws/twin` |

### API

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/health`, `/api/config` | 상태, 장면·장비 기하 |
| POST | `/api/runs` | `{mode: live/replay, seed, autopilot, run_id}` |
| POST | `/api/runs/stop` | 종료 + 리포트 |
| GET | `/api/runs`, `/api/runs/{id}`, `/api/runs/{id}/report` | 목록·메타·리포트 |
| GET | `/api/runs/{id}/series?keys=&t_from=&t_to=` | 기록 시계열 (다운샘플) |
| POST | `/api/control`, `/api/estop`, `/api/estop/reset` | 조종·비상 정지 |
| POST | `/api/faults` | 런타임 fault 주입 (라벨 기록) |
| POST | `/api/replay/{play,pause,seek,speed}` | 리플레이 제어 |
| GET | `/api/twin/state`, `/api/twin/history`, `/api/twin/keys`, `/api/twin/events` | twin 조회 |
| WS | `/ws/twin` | 10 Hz 스냅샷 push, 클라이언트 → `{type: control/estop/ping}` |

API 핸들러는 스레드풀, tick 루프는 이벤트 루프에서 돌기 때문에 Engine 변경 메서드는 RLock으로 직렬화한다.
