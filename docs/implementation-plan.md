# TwinPilot AI — 설계 및 구현 계획

> 시뮬레이션 기반 디지털 트윈 프로토타입 (Level 1 + Level 2 데이터 구조).
> 근거: `office-hours.md`, `mvp-proposal.md`, `decision-log.md`. 단계 지시: `prompts/phase-N-*.md`.

## 작업 절차

1. 작업 시작 전 아래 표의 상태를 `진행중`으로 바꾸고 commit + push.
2. 구현.
3. 해당 행의 **스모크 테스트** 실행.
4. 통과 → `완료` + 비고에 결과 한 줄 → commit + push. 실패 → `진행중` 유지, 비고에 원인.

상태 값: `대기` / `진행중` / `완료` / `보류`

---

## 1. 전체 설계

### 1.1 데이터 흐름

```
키보드/게임패드 (frontend)
   │ ControlCommand (WS)
   ▼
Control Mapper ──► Control Safety Layer (결정론, AI 입력 없음)
   │ 검증·제한된 명령
   ▼
SimulatorAdapter ── #1 PythonSim (Phase 1)  /  #2 Gazebo (Phase 5)
   │ TelemetryRecord 스트림 (단위·sim time·sequence)
   ▼
TelemetrySource ── LiveSimSource / ReplaySource / GazeboSource
   │
   ▼
Twin State Store ──► Run Recorder (JSONL → Parquet, SQLite 인덱스)
   │      │
   │      └──► Anomaly Service (규칙 + Isolation Forest, 읽기 전용)
   ▼
FastAPI REST + WS /ws/twin ──► React 대시보드 (3D · 상태 · AI · 분석/리플레이)
```

### 1.2 디렉토리 구조

```
twinpilot-ai/
├─ pyproject.toml              # uv workspace 루트 (ruff·pytest 설정)
├─ sim/                        # twinpilot_sim — 시뮬레이터 코어 (Phase 1)
│  ├─ pyproject.toml
│  ├─ twinpilot_sim/
│  │  ├─ schema.py             # TelemetryRecord, ControlCommand, FaultSpec, RunMetadata
│  │  ├─ units.py              # 허용 단위
│  │  ├─ params/excavator.yaml # 물리 파라미터 단일 소스
│  │  ├─ params.py
│  │  ├─ adapter.py            # SimulatorAdapter Protocol
│  │  ├─ kinematics.py         # 정기구학
│  │  ├─ world.py              # 구역·운반물·금지구역·작업자, attach 규칙
│  │  ├─ simulator.py          # PythonSim (dt=0.02 s, seed)
│  │  ├─ control/
│  │  │  ├─ mapper.py          # 키/축 → ControlCommand
│  │  │  └─ safety.py          # Safety Layer
│  │  ├─ faults.py             # Fault Injection
│  │  ├─ operator.py           # Scripted Operator
│  │  ├─ recorder.py           # JSONL/meta/Parquet
│  │  └─ cli.py                # twinpilot-sim
│  └─ tests/
├─ backend/                    # FastAPI (Phase 2~3)
│  ├─ pyproject.toml
│  ├─ app/
│  │  ├─ main.py  core/  api/
│  │  ├─ telemetry/            # TelemetrySource: live / replay
│  │  ├─ twin/                 # Twin State Store
│  │  ├─ safety/  tasks/  runs/
│  │  ├─ replay/
│  │  └─ anomaly/              # 실시간 탐지 서비스 (Phase 3)
│  └─ tests/
├─ ai/                         # 이상 탐지 (Phase 3)
│  ├─ pyproject.toml
│  ├─ twinpilot_ai/
│  │  ├─ features/  anomaly/  evaluation/  training/
│  └─ tests/
├─ frontend/                   # React + TS + Vite (Phase 4)
│  └─ src/{components,features/{control,telemetry,anomalies,safety,tasks,replay},services,store,types}
├─ ros2_ws/src/                # Phase 5 (선택)
├─ simulation/
│  ├─ tasks/excavator_carry.yaml
│  └─ fault-scenarios/*.yaml
├─ datasets/manifests/         # 매니페스트만 커밋, 데이터는 제외
├─ evals/                      # anomaly/safety/synchronization cases
├─ scripts/                    # 스모크 스크립트
├─ runs/  reports/             # 생성물 (git 제외)
├─ docs/
└─ .github/workflows/ci.yml
```

### 1.3 핵심 인터페이스

```python
class SimulatorAdapter(Protocol):
    def reset(self, seed: int, task: TaskConfig) -> None: ...
    def step(self, command: SafeCommand) -> list[TelemetryRecord]: ...
    def ground_truth(self) -> GroundTruth: ...      # 안전 규칙·작업 평가용
    @property
    def sim_time(self) -> float: ...

class TelemetrySource(Protocol):                    # backend
    async def records(self) -> AsyncIterator[TelemetryRecord]: ...

class AnomalyDetector(Protocol):                    # ai
    model_version: str
    def fit(self, X) -> None: ...
    def score(self, X) -> ndarray: ...
    def explain(self, x) -> list[str]: ...
```

### 1.4 경계 규칙

- `twinpilot_sim.control.safety` 는 `twinpilot_ai`, `app.anomaly` 를 import 하지 않음 (테스트로 강제).
- 모든 TelemetryRecord 는 단위 필수, `quality` 로 simulated / replayed / real 구분.
- sim time 과 wall time 분리 저장.
- 데이터·모델·run 기록은 git 제외. CI 는 소형 fixture 만 사용.

---

## 2. 단계별 작업

### Phase 1 — Core 시뮬레이터 (`prompts/phase-1-core-sim.md`)

| ID | 작업 | 산출물 | 스모크 테스트 | 상태 | 비고 |
|----|------|--------|---------------|------|------|
| P1-1 | 저장소 골격 · uv workspace · 기본 docs · CI | pyproject, .gitignore, README, LICENSE, ci.yml, docs/* | `uv run ruff check . && uv run pytest` | 완료 | ruff·format·pytest 통과 (1 passed) |
| P1-2 | Telemetry 스키마 · 단위 · 파라미터 | schema.py, units.py, excavator.yaml | 스키마 테스트 (단위 누락·잘못된 quality 거부) | 완료 | 스키마·파라미터 테스트 14 passed |
| P1-3 | 시뮬레이터 (동역학 · 센서 · 월드 · attach) | kinematics, world, simulator | 10 s 실행 → 전 센서 레코드 생성, 한계 내 관절 | 완료 | 10 s 실행 전 센서 25/5 Hz·관절 한계·attach/deliver 테스트 통과 (22 passed) |
| P1-4 | Control Mapper · Safety Layer | control/mapper.py, safety.py | 한계·변화율·타임아웃·E-stop·작업자·금지구역·경계 테스트 | 완료 | 매퍼·안전 규칙 12종·AI import 금지 테스트 통과 (34 passed) |
| P1-5 | Fault Injection · Scripted Operator | faults.py, operator.py, fault-scenarios/*.yaml | seed 1~3 운반 3개 완료, fault 5종 신호 변화 | 완료 | seed 1~3 운반 3개 완료(약 47 s), fault 5종 신호 변화 확인 (44 passed) |
| P1-6 | Recorder · CLI · 결정성 | recorder.py, cli.py | CLI 정상+JOINT_LAG run 생성, 동일 seed 해시 일치 | 완료 | CLI 정상·JOINT_LAG 120 s run 생성(각 103,200 레코드), 재실행 sha256 일치 (47 passed) |

### Phase 2 — Backend · Twin (`prompts/phase-2-backend-twin.md`)

| ID | 작업 | 산출물 | 스모크 테스트 | 상태 | 비고 |
|----|------|--------|---------------|------|------|
| P2-1 | FastAPI 골격 · TelemetrySource (Live/Replay) | app/main.py, telemetry/ | Live·Replay 동일 필드 집합, seek 일치 | 완료 | Live·Replay 동일 상태 구조, seek 값 일치, 배속·정지 테스트 통과 (4 passed) |
| P2-2 | Twin State Store · 시간 지표 | twin/store.py | 역순 정렬·stale·누락 카운트 테스트 | 완료 | sim time 정렬·늦은 레코드·누락·stale·지연 측정 테스트 통과 (10 passed) |
| P2-3 | Safety 연동 · 작업 평가 · Run 관리 | safety/, tasks/, runs/ | fixture run 지표 검증, 타임아웃 정지 | 완료 | live 리포트(점수+원시 지표)·타임아웃 정지·E-stop·런타임 fault 라벨·replay 테스트 통과 (62 passed) |
| P2-4 | REST · WebSocket · 스모크 스크립트 | api/, scripts/ws_smoke.py | 서버 기동 → run → 제어 → WS → 리포트 → replay | 완료 | uvicorn 기동 후 ws_smoke.py SMOKE OK (WS 10 Hz·제어·fault·타임아웃·리포트·replay), pytest 65 passed |

### Phase 3 — 이상 탐지 (`prompts/phase-3-anomaly-ai.md`)

| ID | 작업 | 산출물 | 스모크 테스트 | 상태 | 비고 |
|----|------|--------|---------------|------|------|
| P3-1 | 데이터셋 생성 · 매니페스트 · run 단위 분할 | training/make_dataset.py, manifests/v1.yaml | 생성 완료, 분할 누수 테스트 | 대기 | |
| P3-2 | 특징량 | features/ | 결정성·창 경계 테스트 | 대기 | |
| P3-3 | 탐지기 3종 (Threshold · Z-score · IF) | anomaly/ | fixture 학습·추론 | 대기 | |
| P3-4 | 평가 · 실험 1~3 기록 | evaluation/, reports/eval-v1.md, experiments.md | twinpilot-eval 실행, 표 생성 | 대기 | |
| P3-5 | 실시간 탐지 서비스 · CI fixture · evals | backend/app/anomaly, evals/anomaly-cases.yaml | JOINT_LAG 주입 시 anomaly_state 변화 | 대기 | |

### Phase 4 — 대시보드 (`prompts/phase-4-dashboard.md`)

| ID | 작업 | 산출물 | 스모크 테스트 | 상태 | 비고 |
|----|------|--------|---------------|------|------|
| P4-1 | Vite 골격 · 서비스 · store · 타입 · CI | frontend/ | typecheck · test · build | 대기 | |
| P4-2 | 조종 입력 · 3D 뷰 | features/control, 3D 씬 | 키 매핑 테스트, 브라우저 조종 반영 | 대기 | |
| P4-3 | 상태 · AI 화면 · Fault 패널 | features/telemetry, anomalies | fault 주입 → 점수 그래프 상승 | 대기 | |
| P4-4 | 분석 · 리플레이 · 보고서 · README | features/replay, tasks | 데모 4단계 실제 수행 | 대기 | |

### Phase 5 — Gazebo Adapter (선택, `prompts/phase-5-gazebo-adapter.md`)

| ID | 작업 | 산출물 | 스모크 테스트 | 상태 | 비고 |
|----|------|--------|---------------|------|------|
| P5-1 | Xacro · 월드 SDF · Docker | ros2_ws/src/twinpilot_description, _gazebo | check_urdf, SDF 검사 | 대기 | |
| P5-2 | ros2_control · bridge | _control, _bridge | headless 10 s, telemetry 수신 | 대기 | |
| P5-3 | GazeboSource 연동 | backend telemetry | 대시보드 GAZEBO 모드 표시 | 대기 | |
