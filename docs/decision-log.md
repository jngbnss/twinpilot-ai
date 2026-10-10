# Decision Log

| ID | 일자 | 결정 | 이유 | 대안 |
|----|------|------|------|------|
| D-001 | 2026-10-11 | 첫 시뮬레이터는 Python 결정론 시뮬레이터. Gazebo는 `SimulatorAdapter` 두 번째 구현으로 Phase 5 | Windows 개발 환경, 재현성, 병렬 개발 | Gazebo 우선 |
| D-002 | 2026-10-11 | 운반물 집기는 규칙 기반 attach/detach | Gazebo 강체 파지 불안정, 데모 신뢰성 | 접촉 물리 |
| D-003 | 2026-10-11 | 3D 표시는 Three.js 로우폴리 박스 모델 | 브라우저 단일 화면 데모 | Gazebo GUI |
| D-004 | 2026-10-11 | 첫 AI 범위는 센서 시계열 이상 탐지 (임계값 / Z-score / Isolation Forest) | 핵심 가설 집중 | 객체 탐지·LLM 동시 진행 |
| D-005 | 2026-10-11 | 기록은 JSONL + Parquet, 메타데이터 SQLite | 단순, 의존성 최소 | TimescaleDB |
| D-006 | 2026-10-11 | 위험 판단은 시뮬레이터 ground truth 좌표 기반 규칙 | 객체 탐지 없이도 안전 규칙 검증 | 카메라 탐지 |
| D-007 | 2026-10-11 | 이상 탐지 결과는 Safety Layer 입력으로 사용하지 않음 | AI가 안전 규칙을 바꾸거나 우회 불가 원칙 | AI 기반 자동 정지 |
| D-008 | 2026-10-11 | 평가 데이터 분할은 run 단위 | 시계열 창 누수 방지 | 창 단위 랜덤 분할 |
| D-009 | 2026-10-11 | Python 환경은 uv, 프론트는 Node 24 + Vite | Windows에 Python 미설치, 재현 가능한 lock | pip/venv |
| D-010 | 2026-10-11 | ControlCommand는 전체 축 스냅샷(-1..1) 형식 | 축별 타임아웃·변화율 제한을 일관되게 적용 | 명령당 1축 메시지 |
| D-011 | 2026-10-11 | 단위 표기는 ASCII (`Nm`, `degC`, `m/s^2`) | 파일·URL·CSV 호환 | 유니코드 기호 |
| D-012 | 2026-10-11 | 물리 50 Hz, 고속 센서 25 Hz, 저속 센서 5 Hz | 120 s run 레코드 수를 약 10만으로 제한(데이터셋 용량) | 센서 50/10 Hz |
| D-013 | 2026-10-11 | 기본 작업 시나리오는 패키지 내부 `twinpilot_sim/scenarios/`, 변형은 `simulation/tasks/` | 설치 위치와 무관하게 로드 | 저장소 경로 고정 |
| D-014 | 2026-10-11 | CLI·서버는 `python -m` 으로 실행 | 로컬 Windows 애플리케이션 제어 정책이 uv 생성 `.exe` 진입점 차단 (os error 4551) | console script exe |
| D-015 | 2026-10-11 | Isolation Forest를 NumPy로 직접 구현 (`twinpilot_ai.anomaly.iforest`), scikit-learn은 CI 동등성 테스트용 선택 의존성 | 개발 PC의 Smart App Control이 scikit-learn·SciPy·일부 numpy 확장 DLL을 버전 무관하게 차단 | 정책 해제, WSL에서 학습 |
| D-016 | 2026-10-11 | 운영 모델은 val F1로 선정 (현재 Tail-normalized Z-score + residual 특징), IF는 비교 기준으로 유지 | 실험 1~2: IF가 FPR 1%에서 F1 0.03 이하, 고장이 소수 특징에 집중 | IF 운영 모델 |
