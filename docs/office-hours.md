# Office Hours — TwinPilot AI

일자: 2026-10-11
입력: TwinPilot AI 프로젝트 개발 프롬프트 (35개 섹션)
진행 방식: 모든 갈림길에서 추천안(Recommended) 채택

---

## 1. 한 줄 재정의

> 고장 주입이 가능한 굴착기 시뮬레이터에서 **시간 정렬된 센서 상태(twin state)** 를 만들고,
> 규칙 기준선 대비 **이상 탐지 모델을 재현 가능하게 평가**하는 디지털 트윈 확장형 프로토타입.

굴착기 조종은 데이터를 만드는 수단이다. 프로젝트의 핵심 자산은 **telemetry 스키마 + fault injection + replay + 평가 파이프라인**(= `twintrace`)이다.

## 2. 강점

- 디지털 트윈 표현 원칙(Level 1/2/3)과 금지 표현이 명확하다. 과장 리스크가 낮다.
- 안전 규칙(결정론)과 AI 분리 원칙이 처음부터 있다.
- "규칙 기준선 → Isolation Forest → 딥러닝" 순서가 맞다.
- 고장 라벨을 정답으로 저장하는 평가 설계가 있다.

## 3. 문제점 (냉정한 지적)

| # | 문제 | 영향 |
|---|------|------|
| P1 | 첫 단계가 ROS 2 + Gazebo + ros2_control 셋업이다. 개발 환경은 Windows 11, WSL Ubuntu는 26.04(Jazzy 공식 타깃은 24.04). | 수주를 환경 구성에 소모. 백엔드·AI·대시보드가 전부 Gazebo에 막힘. |
| P2 | 35개 섹션 전부가 MVP처럼 쓰여 있다(객체 탐지, LLM 설명, 7개 화면, twintrace OSS). | 4단계 프롬프트로 끝까지 못 감. |
| P3 | Gazebo 굴착기 버킷으로 강체를 "집는" 것은 물리적으로 불안정(관통, 미끄러짐). | 작업 시나리오가 데모에서 깨짐. |
| P4 | 이상 탐지 평가는 **결정적 데이터**가 핵심인데, Gazebo 물리는 실행마다 미세하게 달라진다. | CI 재현성 조건(섹션 29) 충족이 어려움. |
| P5 | 객체 탐지·LLM은 핵심 가설(센서 이상 탐지)과 무관한 범위 확장이다. | 시간 분산. |

## 4. 핵심 질문과 채택한 답 (Recommended)

**Q1. 첫 시뮬레이터를 Gazebo로 할 것인가?**
→ **추천: 아니오. Python 결정론 시뮬레이터(SimulatorAdapter #1)로 시작, Gazebo는 Adapter #2.**
근거: 백엔드·AI·대시보드를 Windows에서 즉시 개발 가능. 고정 seed로 완전 재현. Gazebo가 붙어도 `SimulatorAdapter` 인터페이스 뒤로 숨기면 나머지는 그대로.

**Q2. 운반물 집기는 물리로 할 것인가?**
→ **추천: 규칙 기반 attach.** 버킷 끝이 운반물 반경 r 안 + 버킷 닫힘 각도 이상 → attach. 버킷 열면 detach, 낙하 위치로 판정. 문서에 가정으로 명시.

**Q3. 3D가 필요한가?**
→ **추천: 대시보드는 Three.js 박스 모델(로우폴리).** 물리는 Python 시뮬레이터, 렌더는 브라우저. Gazebo GUI는 Phase 5.

**Q4. 첫 AI 범위는?**
→ **추천: 센서 시계열 이상 탐지만.** 고정 임계값 / Z-score / Isolation Forest 3종 비교. 객체 탐지·LLM은 MVP 제외(로드맵).

**Q5. 저장 포맷?**
→ **추천: 실행 기록 JSONL(append) + Parquet 변환, 메타는 SQLite.** TimescaleDB 제외.

**Q6. 위험 객체 탐지 대체는?**
→ **추천: 시뮬레이터가 아는 실제 좌표(ground truth)로 거리 기반 규칙 판단.** 카메라 탐지는 adapter 자리만 남김.

**Q7. 언어·런타임?**
→ Python 3.12 (uv 관리), Node 24 + Vite + React + TS. Windows에 실제 Python 미설치 → `uv`로 설치.

## 5. 범위 결정

**MVP 포함 (4 Phase)**
- Python 굴착기 시뮬레이터 (트랙·스윙·붐·암·버킷, 1차 지연 관절 동역학, 가상 센서)
- Control Mapper + Control Safety Layer (결정론)
- Fault Injection (9종 중 MVP 5종: JOINT_LAG, FRICTION_INCREASE, SENSOR_NOISE, SENSOR_STUCK, TEMP_RISE)
- Telemetry 스키마 / Twin State Store / 실행 기록 / Replay(Level 2)
- 작업 시나리오 A→B 3개 운반, 성과 지표, 위험 이벤트
- 이상 탐지 3종 비교 + 고정 평가셋 + 지표
- 웹 대시보드 (조종 / 상태 / AI / 분석·리플레이)

**MVP 제외 → 로드맵**
- Gazebo/ROS 2 Adapter (Phase 5, Docker `ros:jazzy` 기반)
- 객체 탐지, LLM 설명, 딥러닝 오토인코더, twintrace 분리 배포

## 6. 가장 큰 위험 Top 5

1. **ROS/Gazebo 환경 리스크** — Adapter 분리로 격리. Phase 5는 Docker로만.
2. **과장 표현 리스크** — UI 전역 `SIMULATED SENSOR` 배지, README 첫 줄에 Level 1 명시.
3. **평가 누수** — 같은 run의 창(window)이 train/test에 섞이지 않게 **run 단위 분할**.
4. **쉬운 고장만 탐지되는 착시** — severity 구간별·유형별 성능 분리 보고. SENSOR_STUCK 같은 어려운 유형 포함.
5. **안전 계층 우회** — 이상 탐지 결과는 Safety Layer에 *입력되지 않음*(읽기 전용 표시만). 테스트로 강제.

## 7. 실행 계획

`docs/prompts/` 의 프롬프트를 순서대로 Claude Code에 넣는다.

| Phase | 프롬프트 | 결과물 | 데모 |
|-------|----------|--------|------|
| 1 | `phase-1-core-sim.md` | 시뮬레이터, 스키마, Control/Safety, Fault, 기록, CI | CLI로 시나리오 실행 → JSONL 생성 |
| 2 | `phase-2-backend-twin.md` | FastAPI, Twin State Store, WebSocket, 작업·성과, Replay | WebSocket으로 실시간 상태 수신 |
| 3 | `phase-3-anomaly-ai.md` | 데이터셋 생성, 특징량, 3종 모델, 평가, 실시간 탐지 | 평가 리포트 + API 이상 점수 |
| 4 | `phase-4-dashboard.md` | React 대시보드, 3D, 조종, AI·리플레이 화면 | 브라우저에서 조종→고장→탐지→비교 |
| 5 (선택) | `phase-5-gazebo-adapter.md` | ROS 2 Jazzy + Gazebo Adapter (Docker) | 같은 대시보드에 Gazebo 데이터 |

각 Phase 완료 조건: `ruff check` 통과, `pytest` 통과, 해당 데모 명령 실제 실행.
