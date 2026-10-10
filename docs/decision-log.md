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
