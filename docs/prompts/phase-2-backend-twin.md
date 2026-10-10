# Phase 2 — Backend · Twin State Store · WebSocket · 작업 평가 · Replay

---

TwinPilot AI Phase 2를 구현해줘. `docs/` 결정과 Phase 1 코드(`sim/`)를 먼저 읽고 재사용한다. 스키마를 복제하지 말고 `twinpilot_sim.schema`를 import한다. 이 Phase에서는 AI 모델과 프론트를 만들지 않는다.

## 구현 범위 (`backend/`, uv workspace 멤버 추가)
1. **FastAPI 앱** `backend/app/main.py`, 설정은 pydantic-settings + `.env.example`.
2. **TelemetrySource 인터페이스** (`app/telemetry/source.py`): `LiveSimSource`(Phase 1 시뮬레이터를 asyncio 루프에서 50 Hz 실시간 구동), `ReplaySource`(`runs/<id>/telemetry.jsonl` 재생: play/pause/speed 0.25~8×/seek). 두 소스 모두 같은 레코드 스트림을 내보낸다. Replay 레코드는 `quality="replayed"`.
3. **Twin State Store** (`app/twin/store.py`): `mvp-proposal.md` 7절 구조. simulation_time 기준 정렬, source별 stale 판정(기대 주기 × 3), sequence 누락·역전 카운트, 스냅샷, 링버퍼 히스토리(최근 120 s). 시간 지표: 생성→백엔드 수신 지연, 누락률, 역전률.
4. **Safety 연동** (`app/safety/`): Phase 1 Safety Layer를 래핑. 위험 이벤트를 이벤트 로그에 기록. E-stop API.
5. **작업 평가** (`app/tasks/`): 운반 수, 완료 시간, 충돌, 작업자 위험거리 진입, 낙하, 전복 위험, 불필요 조작(방향 반전 횟수), 에너지, 평균 명령 변화량. 종합 점수 + 원시 지표 함께 반환.
6. **Run 관리**: 세션 시작/종료 시 `runs/`에 기록(Phase 1 recorder 재사용), SQLite에 run 메타 인덱스. Fault 주입 API (실행 중 fault 추가 → 라벨 기록).
7. **API**
   - `POST /api/runs` (mode: live|replay, seed, scenario) / `POST /api/runs/{id}/stop`
   - `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/report`
   - `POST /api/control` (ControlCommand) / `POST /api/estop` / `POST /api/estop/reset`
   - `POST /api/faults` (FaultSpec)
   - `POST /api/replay/{id}/play|pause|seek|speed`
   - `GET /api/twin/state`, `GET /api/twin/history?metrics=...&from=&to=`
   - `WS /ws/twin`: 10 Hz로 TwinState 스냅샷 push, 클라이언트는 같은 소켓으로 ControlCommand 전송 가능
8. 실행: `uv run uvicorn app.main:app --reload --app-dir backend`. CORS는 `http://localhost:5173`만 허용.

## 테스트
- Twin store: 순서 뒤바뀐 레코드 정렬, stale 판정, 누락 카운트.
- Live와 Replay가 같은 seed run에 대해 동일한 TwinState 필드 집합을 생성.
- Replay seek 후 상태가 해당 시점 기록과 일치.
- 통신 타임아웃: WS 제어 입력 중단 0.5 s 후 장비 정지.
- E-stop은 이상 탐지 상태와 무관하게 동작.
- FastAPI TestClient로 주요 엔드포인트, WebSocket 스냅샷 수신.
- 작업 평가 지표를 고정 fixture run으로 검증.

## 완료 조건
ruff·pytest 통과. 서버 실행 후 스크립트(`scripts/ws_smoke.py`)로 live run 생성 → 제어 명령 몇 개 전송 → WS 스냅샷 수신 → 정지 → 리포트 출력 → 같은 run replay 재생까지 실제 수행해 결과를 보여줘. `docs/architecture.md` 갱신, decision-log 추가.
