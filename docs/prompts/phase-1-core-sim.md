# Phase 1 — Core 시뮬레이터 · 스키마 · Control/Safety · Fault · 기록

아래를 그대로 Claude Code에 붙여넣는다.

---

TwinPilot AI Phase 1을 구현해줘. 먼저 `docs/office-hours.md`, `docs/mvp-proposal.md`, `docs/decision-log.md`를 읽고 그 결정을 따른다. 이 Phase에서는 백엔드 서버·AI·프론트를 만들지 않는다.

## 환경
- Python 3.12를 `uv`로 관리한다 (Windows에 Python 미설치 상태면 `uv` 설치 → `uv python install 3.12`).
- 루트에 uv workspace `pyproject.toml`. 패키지: `sim/` (twinpilot_sim). 이후 Phase에서 `backend/`, `ai/`가 추가된다.
- 개발 의존성: pytest, ruff, pydantic v2, numpy, pyyaml, pyarrow.

## 구현 범위
1. **저장소 골격**: `.gitignore`(datasets/raw, runs/, *.parquet, *.onnx, *.joblib, node_modules, .venv), `README.md`(첫 줄에 "시뮬레이션 기반 디지털 트윈 프로토타입 — Level 1" 명시), `LICENSE`(MIT), `.env.example`, `docs/` 기존 파일 유지 + `architecture.md`, `digital-twin-levels.md`, `telemetry-schema.md`, `simulation-assumptions.md`, `safety-limitations.md`, `licenses.md`, `experiments.md` 생성.
2. **Telemetry 스키마** (`sim/twinpilot_sim/schema.py`): `TelemetryRecord`, `ControlCommand`, `FaultSpec`, `RunMetadata`. 단위는 허용 enum, 빈 단위 거부. `quality ∈ {simulated, replayed, real}`.
3. **파라미터** `sim/twinpilot_sim/params/excavator.yaml`: 링크 길이·질량·관절 한계·최대 속도·시정수. 값의 출처/가정을 `simulation-assumptions.md`에 기록 (20톤급 굴착기 공개 제원 수준의 근사라고 명시).
4. **시뮬레이터** (`sim/twinpilot_sim/simulator.py`): 고정 dt=0.02 s, seed 고정 RNG.
   - 트랙: 차동 구동(좌/우 속도 → 전진·yaw), 지면 평지.
   - 관절(swing/boom/arm/bucket): 속도 명령 → 1차 지연 응답 → 위치 적분 → 한계 클램프.
   - effort: 링크·payload 중력 토크 근사 + 점성 마찰.
   - 정기구학으로 버킷 끝 월드 좌표 계산.
   - 가상 장비 센서: 유압 온도(부하 적분 + 냉각), 엔진 부하, 진동 RMS, 에너지 누적.
   - IMU: roll/pitch(무게중심 이동에 따른 근사), yaw, 각속도, 가속도 + 소량 노이즈.
   - 작업 월드: 구역 A/B, 운반물 3개, 금지 구역, 작업자 모형. 규칙 기반 attach/detach.
   - `step(command) -> list[TelemetryRecord]`, `ground_truth()` (객체·작업자 실제 좌표).
   - 인터페이스 `SimulatorAdapter` (Protocol)로 정의하고 이 클래스가 첫 구현.
5. **Control Mapper + Safety Layer** (`sim/twinpilot_sim/control/`): 키 입력/게임패드 축 → `ControlCommand` 변환, sequence 검증. Safety: 관절 위치·속도·변화율 제한, 0.5 s 명령 타임아웃 시 정지, E-stop 래치, 롤/피치 한계 초과 시 작업 관절 정지, 작업자 5 m 이내 주행 차단, 금지 구역 진입 차단. 결과에 적용된 규칙 목록과 위험 등급(INFO/CAUTION/WARNING/EMERGENCY) 반환. AI 모듈 import 금지.
6. **Fault Injection** (`sim/twinpilot_sim/faults.py`): JOINT_LAG, FRICTION_INCREASE, SENSOR_NOISE, SENSOR_STUCK, TEMP_RISE. YAML 시나리오 로드 (`simulation/fault-scenarios/joint_lag_bucket.yaml` 등 5개 예시). 동일 seed → 동일 출력.
7. **Scripted Operator** (`sim/twinpilot_sim/operator.py`): 운반물 3개를 A→B로 옮기는 상태머신 자동 조종기. 속도 배율·하중 변형 파라미터.
8. **Run Recorder** (`sim/twinpilot_sim/recorder.py`): `runs/<run_id>/telemetry.jsonl`, `meta.json`(seed, 파라미터 해시, fault 라벨, 결과), Parquet 변환 함수.
9. **CLI**: `uv run twinpilot-sim run --scenario normal --seed 1 --duration 120` 및 `--fault simulation/fault-scenarios/joint_lag_bucket.yaml`.
10. **CI** `.github/workflows/ci.yml`: `permissions: contents: read`, uv 설치, ruff, pytest.

## 테스트 (필수)
- 스키마: 단위 누락·잘못된 quality 거부.
- 결정성: 같은 seed·fault로 2회 실행한 JSONL 해시 동일.
- 관절 한계/속도 한계/변화율 제한/타임아웃/E-stop/작업자 거리/금지 구역 각각 단위 테스트.
- Scripted operator가 fault 없이 seed 1~3에서 운반물 3개 완료.
- 각 fault 유형이 대상 신호를 실제로 변화시키는지(정상 run 대비) 검증.
- `tests/test_boundaries.py`: `twinpilot_sim.control`이 `ai` 패키지를 import하지 않음.

## 완료 조건
`uv run ruff check .` 와 `uv run pytest` 통과, CLI로 정상 1개 + JOINT_LAG 1개 run 생성 후 결과 요약(운반 수, 시간, 안전 이벤트 수) 출력. `docs/decision-log.md`에 새 결정 추가. 마지막에 변경 파일 목록과 다음 Phase 주의사항을 보고.
