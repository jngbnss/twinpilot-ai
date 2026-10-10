# Phase 4 — React 대시보드 · 3D · 조종 · AI · 리플레이 · 보고서

---

TwinPilot AI Phase 4를 구현해줘. Phase 2·3 API(`docs/architecture.md`, `backend/app`)를 먼저 읽고 타입을 맞춘다. 백엔드 수정은 필요한 최소한만.

## 스택
React 18 + TypeScript + Vite (`frontend/`), Zustand, Three.js (@react-three/fiber, drei), Apache ECharts, Vitest + Testing Library. 패키지 매니저 npm.

## 구현 범위
1. **서비스 계층** `src/services/`: REST 클라이언트, WS 클라이언트(자동 재연결, 지연 측정), 타입은 `src/types/`에 백엔드 Pydantic과 일치. (가능하면 FastAPI OpenAPI → `openapi-typescript`로 생성)
2. **상태** Zustand store: twin 스냅샷, 연결 상태, 선택 run, replay 커서.
3. **조종** `features/control/`: 키보드(W/S, A/D, Q/E, R/F, T/G, Y/H, Space=E-stop), Gamepad API(좌스틱 주행, 우스틱 붐·암, 트리거 버킷, 숄더 스윙). 20 Hz로 ControlCommand 전송, 입력 표시 HUD. 포커스 잃으면 입력 중단(→ 백엔드 타임아웃 정지).
4. **3D 뷰**: 로우폴리 박스 굴착기(트랙/상부/붐/암/버킷 계층 = 관절 각도 반영), 구역 A/B, 운반물, 금지 구역, 작업자 모형, 작업자 안전 반경 링. 외부 카메라 + 운전석 시점 전환.
5. **화면 (탭)**
   - 조종: 3D + HUD + 작업 목표·진행률 + 큰 E-stop 버튼 + 위험 등급 배너
   - 상태: 관절 각도·속도·effort, IMU, 온도·진동·에너지 게이지. 가상 값엔 `SIMULATED SENSOR` 배지
   - AI: 이상 점수 타임라인(threshold 선, 규칙 경고 마커 별도 표시), affected sensors, 정상 구간/이상 구간 드래그 선택 → compare API 결과 표·그래프. 원인 단정 금지 문구.
   - 분석·리플레이: run 목록, 타임라인 스크럽·재생·배속, 3D·그래프 동기화, 이벤트 마커(fault 라벨, 안전 이벤트, 이상 탐지), 성과 보고서(원시 지표 + 점수), 보고서 Markdown/JSON 다운로드
   - Fault 패널(개발용): 유형·대상·severity 선택해 주입
6. 상단 고정: 연결 상태, run_id, simulation time, 모드(LIVE / REPLAY), "Simulation-based Digital Twin Prototype · Level 1" 표기.
7. CI에 `npm ci`, `npm run typecheck`, `npm run test`, `npm run build` 추가.

## 테스트
- 키 매핑 → ControlCommand 변환, 포커스 이탈 시 중단.
- E-stop 버튼이 AI 상태와 무관하게 API 호출.
- WS 메시지 → store 갱신.
- SIMULATED SENSOR 배지 렌더.

## 완료 조건
typecheck·test·build 통과, 백엔드 pytest 여전히 통과. 백엔드 + `npm run dev` 띄운 뒤 다음 데모를 실제로 수행하고 결과 보고:
1. live run 시작, 키보드로 조종해 운반물 1개 이상 이동
2. JOINT_LAG(bucket, 0.8) 주입 → 규칙 경고·이상 점수 상승 확인
3. 정상/이상 구간 비교
4. run 종료 → 보고서 확인 → 같은 run 리플레이
README에 빠른 시작(백엔드·프론트 실행 명령, 데모 순서)과 스크린샷 자리 추가. decision-log 추가.
