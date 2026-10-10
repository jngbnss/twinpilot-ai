# TwinPilot AI

> **시뮬레이션 기반 디지털 트윈 프로토타입 — Level 1 (Simulation)**
> 실제 굴착기·실제 센서와 연결되어 있지 않다. 모든 센서값은 `SIMULATED SENSOR` 이다.

가상 굴착기를 조종하며 장비 상태와 작업 성과를 확인하고, 고장 주입(fault injection)으로 만든 센서 이상을
규칙 기준선과 이상 탐지 모델로 탐지·평가하는 프로젝트. 실제 센서 연동(Level 2 Replay / Level 3 Connected)을
고려한 데이터 구조로 설계했다.

## 상태

진행 상황: [`docs/implementation-plan.md`](docs/implementation-plan.md)

## 구조

| 경로 | 내용 |
|------|------|
| `sim/` | 결정론 Python 굴착기 시뮬레이터, telemetry 스키마, Control Safety Layer, fault injection |
| `backend/` | FastAPI, Twin State Store, WebSocket, Replay (Phase 2) |
| `ai/` | 특징량, 이상 탐지, 평가 (Phase 3) |
| `frontend/` | React 대시보드 (Phase 4) |
| `ros2_ws/` | ROS 2 Jazzy + Gazebo Adapter (Phase 5, 선택) |
| `docs/` | 설계·결정·실험 기록 |

## 빠른 시작

```bash
uv sync --all-packages
uv run ruff check .
uv run pytest
```

## 범위 밖

실제 중장비 원격 제어, 안전 인증, 정밀 토사·유압 해석, 실제 고장 원인 진단.
자세한 한계는 [`docs/safety-limitations.md`](docs/safety-limitations.md).

## 라이선스

MIT. 사용 OSS 라이선스는 [`docs/licenses.md`](docs/licenses.md).
