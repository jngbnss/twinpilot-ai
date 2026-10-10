# MVP 제안서 (원 프롬프트 섹션 35 응답)

Office Hours 추천안(`office-hours.md`, `decision-log.md`) 기준.
표현 원칙: **시뮬레이션 기반 디지털 트윈 프로토타입 (Level 1 + Level 2 데이터 구조)**.

## 1. 작업 시나리오

- 맵 40 m × 40 m 평지. 작업 구역 A(원형 r=3 m, 중심 (-8, 6)), 작업 구역 B(원형 r=3 m, 중심 (8, 6)).
- 운반물 3개(상자 300 kg, 돌 450 kg, 토사 블록 600 kg) A 안에 배치.
- 금지 구역 1개(사각형), 작업자 모형 1개(고정 위치, 안전 반경 5 m).
- 제한 시간 300 s(simulation time).
- 성공: 3개 B 안에 내려놓음 ∧ 제한 시간 내 ∧ 금지 구역 진입 0 ∧ 작업자 충돌 0 ∧ 전복 0.
- 집기: 버킷 끝이 운반물 중심 0.8 m 이내 + 버킷 각도 ≥ 닫힘 임계 → attach. 열면 detach.

## 2. 패키지 구조 (Phase 1~4 / Phase 5)

```
sim/            twinpilot_sim      Python 시뮬레이터 (SimulatorAdapter #1)
backend/app/    FastAPI, twin, telemetry, tasks, safety, replay
ai/             features, anomaly, evaluation, training
frontend/       React + TS + Vite
ros2_ws/src/    (Phase 5) twinpilot_description, _gazebo, _control, _bridge
```

ROS 2 패키지(Phase 5): `twinpilot_description`(Xacro), `twinpilot_gazebo`(world, launch), `twinpilot_control`(ros2_control 설정), `twinpilot_bridge`(ROS 토픽 → 공통 telemetry → 백엔드 WebSocket). sensors/faults는 Python 공통 모듈 재사용.

## 3. URDF·Xacro 모델 구조 (Phase 5, Python 시뮬레이터도 동일 파라미터 사용)

```
base_link (트랙 프레임)
├─ left_track_joint  (continuous, velocity)
├─ right_track_joint (continuous, velocity)
└─ swing_joint (continuous, yaw) → upper_body
   └─ boom_joint (revolute -0.5..1.2 rad) → boom (5.7 m)
      └─ arm_joint (revolute -2.6..-0.5 rad) → arm (2.9 m)
         └─ bucket_joint (revolute -1.8..1.0 rad) → bucket (1.4 m)
```

파라미터 단일 소스: `sim/twinpilot_sim/params/excavator.yaml`. 시각 메시 / 충돌 박스 분리. 출처·가정은 `docs/simulation-assumptions.md`.

## 4. ros2_control 인터페이스

| Joint | command | state |
|-------|---------|-------|
| left/right_track | velocity | position, velocity |
| swing, boom, arm, bucket | velocity | position, velocity, effort |

컨트롤러: `joint_state_broadcaster`, `forward_velocity_controller`(트랙), `forward_velocity_controller`(작업 관절). Safety Layer는 컨트롤러 **앞단**(Control Mapper)에서 동작.

## 5. 가상 센서와 단위

| source | metric | unit | Hz |
|--------|--------|------|----|
| `<joint>` | position / velocity / effort / command / command_error | rad, rad/s, N·m, rad/s, rad/s | 25 |
| `left_track`,`right_track` | velocity | m/s | 25 |
| `imu` | roll, pitch, yaw / angular_velocity_z / linear_accel_x,y,z | rad, rad/s, m/s² | 25 |
| `base` | pose_x, pose_y | m | 25 |
| `hydraulic` | oil_temperature | °C | 5 |
| `engine` | load | % | 5 |
| `chassis` | vibration_rms | m/s² | 5 |
| `energy` | consumed | kJ | 5 |
| `contact` | bucket_payload / collision | bool(0/1) | event |

전부 `quality: "simulated"`.

## 6. Telemetry Schema

```json
{
  "run_id": "run-20261011-001",
  "timestamp_ns": 1760140800000000000,
  "simulation_time": 14.25,
  "source": "boom_joint",
  "metric": "effort",
  "value": 73.4,
  "unit": "N·m",
  "quality": "simulated",
  "sequence": 481
}
```

Pydantic v2, `unit` 필수(빈 문자열 금지, 허용 단위 enum), `quality ∈ {simulated, replayed, real}`, `sequence` source별 단조 증가.

## 7. Twin State Store

```
TwinState {
  run_id, simulation_time, wall_time_ns,
  vehicle_pose {x, y, yaw, roll, pitch},
  joint_states {joint_id: {position, velocity, effort, command, stamp}},
  sensor_states {"source.metric": {value, unit, stamp, stale: bool}},
  task_state {objects[], delivered, elapsed, status},
  safety_state {level, active_rules[], estop},
  anomaly_state {score, is_anomaly, affected_sensors[], model_version}
}
```

규칙: simulation_time 기준 정렬, source별 기대 주기의 3배 초과 시 `stale`, sequence 역전·누락 카운트. Live/Replay 둘 다 `TelemetrySource` 인터페이스로 공급.

## 8. Fault Injection 시나리오

| type | target | 효과 |
|------|--------|------|
| JOINT_LAG | 관절 | 응답 시정수 × (1 + 4·severity) |
| FRICTION_INCREASE | 관절 | 속도 감쇠·effort 증가, 온도 상승 가속 |
| SENSOR_NOISE | source.metric | 가우시안 노이즈 σ × (1 + 10·severity) |
| SENSOR_STUCK | source.metric | 시작 시점 값 고정 |
| TEMP_RISE | hydraulic | 온도 기울기 +severity·0.5 °C/s |

정의: `simulation/fault-scenarios/*.yaml`. 결과 라벨: `fault_id, type, target, start_time, duration, severity` → run 메타에 저장. seed 고정 시 결정적.

## 9. 이상 탐지

- 기준선 1: 고정 임계값 (metric별 정상 데이터 p99.5)
- 기준선 2: rolling Z-score (창 2 s, |z| > 4)
- 모델 1: Isolation Forest (창 2 s / stride 0.5 s 특징: mean, std, 변화율, command_error 평균·최대)
- 출력: `{timestamp, is_anomaly, anomaly_score, affected_sensors, model_version, threshold}`
- affected_sensors = 특징별 정상 대비 z 상위 3개 (원인 단정 금지 문구 사용)

## 10. 고정 평가 데이터

- 스크립트 기반 자동 조종기(scripted operator)가 작업 시나리오 수행 → 사람 없이 대량 생성.
- 정상 40 run, 고장 60 run (5종 × severity {0.3, 0.6, 0.9} × 4 seed). 짧은 run 120 s.
- 분할: run 단위 train/val/test. 미확인 조건 세트(다른 하중, 다른 속도, 복합 고장) 별도.
- `datasets/manifests/v1.yaml`에 seed·파라미터·해시 기록. 데이터 자체는 Git 제외, 재생성 명령 제공.
- CI용 소형 fixture(정상 2, 고장 2 run)만 커밋.

## 11. 대시보드 화면

1. **조종**: 3D 뷰(Three.js), 조작 입력 표시, 작업 목표, 비상 정지
2. **상태**: 관절 각도, IMU, 온도·진동(SIMULATED SENSOR 배지), 진행률
3. **AI**: 이상 점수 타임라인, 관련 센서, 규칙 경고 vs 모델 결과, 정상 구간 비교
4. **분석·리플레이**: run 선택, 타임라인 스크럽·배속, 이벤트 마커, 성과 보고서

## 12. 안전 규칙과 AI 경계

| Safety Layer (결정론) | AI (참고용) |
|---|---|
| 관절 위치·속도·변화율 제한, 타임아웃 정지, E-stop, 전복 위험(롤·피치), 작업자 최소 거리, 금지 구역 | 이상 점수, 관련 센서 제안 |
| 명령을 **수정·차단** 가능 | 명령에 접근 불가, 표시만 |
| 단위 테스트로 100% 경로 검증 | 성능 지표로 평가 |

AI 모듈은 Safety 모듈을 import할 수 없음(테스트로 강제).

## 13. 역할 구분

- **시뮬레이션**: 동역학, 센서 생성, fault, ground truth
- **ROS(Phase 5)**: 같은 telemetry를 Gazebo에서 발행하는 adapter
- **백엔드**: 수집, twin state, 안전, 작업 평가, 기록·재생, API
- **AI**: 특징량, 모델, 평가, 추론 서비스
- **프론트**: 조종 입력, 시각화, 분석·리플레이

## 14. 7인 팀 역할

| # | 역할 | 담당 |
|---|------|------|
| 1 | Tech Lead / 아키텍처 | 인터페이스, 스키마, decision-log, 리뷰 |
| 2 | Simulation | Python 시뮬레이터, 파라미터, fault injection |
| 3 | ROS/Gazebo | Phase 5 adapter, Xacro, ros2_control |
| 4 | Backend | FastAPI, twin store, replay, safety layer |
| 5 | AI-1 | 데이터셋·특징량·기준선 |
| 6 | AI-2 | Isolation Forest, 평가, experiments.md |
| 7 | Frontend | 대시보드, 3D, 조종 입력 |

## 15. 가장 큰 위험

1. ROS/Gazebo 환경 구성 지연 → Adapter 격리 (D-001)
2. 시뮬레이터를 디지털 트윈으로 과장 → Level 표기, 배지
3. 평가 누수·쉬운 고장 착시 → run 단위 분할, 유형·severity별 보고
4. AI가 안전 경로에 섞임 → 의존성 금지 테스트 (D-007)
5. 실제 장비 제어로의 범위 확장 요구 → MVP 명시 제외, 읽기 전용 확장 순서 고정
