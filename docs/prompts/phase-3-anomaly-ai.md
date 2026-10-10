# Phase 3 — 데이터셋 · 특징량 · 이상 탐지 3종 · 평가 · 실시간 탐지

---

TwinPilot AI Phase 3를 구현해줘. `docs/mvp-proposal.md` 9·10절과 Phase 1·2 코드를 먼저 읽는다. 딥러닝은 쓰지 않는다. 이상 탐지 결과는 Safety Layer로 절대 전달하지 않는다(D-007).

## 구현 범위 (`ai/`, uv workspace 멤버 추가, 의존성: scikit-learn, pandas, joblib)
1. **데이터셋 생성** `ai/training/make_dataset.py`: Phase 1 scripted operator로 정상 40 run + 고장 60 run(5종 × severity {0.3,0.6,0.9} × seed 4) 생성, 각 120 s. 고장 시작 시점은 seed 기반 20~70 s. 미확인 조건 세트(하중 1.5×, 조종 속도 0.6×/1.4×, 복합 고장 2종 동시) 20 run. `datasets/manifests/v1.yaml`에 seed·파라미터·코드 해시·파일 해시. 데이터는 git 제외.
2. **run 단위 분할**: train(정상만) / val / test, 미확인 세트는 별도. 분할 목록을 매니페스트에 기록.
3. **특징량** `ai/features/`: 창 2 s / stride 0.5 s. 원시값 평균, rolling std, 변화율, 이동 평균 편차, `command_error` 평균·최대, 진동·온도 기울기. 특징 세트 이름(raw / +delta / +rolling / +cmd_error)으로 실험 2 지원.
4. **모델** `ai/anomaly/` 공통 인터페이스 `AnomalyDetector{fit, score, predict, explain, model_version}`:
   - `ThresholdDetector` (정상 p99.5)
   - `ZScoreDetector` (rolling, |z|>4)
   - `IsolationForestDetector` (threshold는 val 정상 창 FPR 1% 기준)
   - `explain`: 정상 분포 대비 z 상위 3개 특징 → `affected_sensors`
5. **평가** `ai/evaluation/`: 창 단위 Precision/Recall/F1, 오경보율, 미탐률, run 단위 탐지 지연(고장 시작→첫 탐지, s), 유형별·severity별 표, 미확인 조건 성능. `uv run twinpilot-eval --manifest datasets/manifests/v1.yaml` → `reports/eval-v1.md` + JSON.
6. **실험 기록**: 실험 1(기준선 비교), 실험 2(특징 세트 비교), 실험 3(미확인 조건)을 실제 실행하고 `docs/experiments.md`에 "가설 → 데이터 버전 → 모델 버전 → 학습 조건 → 평가 조건 → 기준선 → 결과 → 실패 원인 → 다음 실험" 형식으로 기록. 오경보·미탐 사례 3개씩 원인 분석.
7. **실시간 탐지 서비스** `backend/app/anomaly/`: 학습된 모델(joblib, `ai/models/`, git 제외)을 로드해 Twin store 히스토리에서 0.5 s마다 창 추론 → `anomaly_state` 갱신 + 이벤트 로그. 규칙 경고(Threshold/Z)와 모델 결과를 둘 다 노출. 모델 없으면 규칙만으로 동작.
   - API: `GET /api/anomalies?run_id=`, `GET /api/anomalies/compare?run_id=&normal_from=&normal_to=&anom_from=&anom_to=` (센서별 평균·표준편차·차이)
   - 문구 템플릿: "<센서>에서 평소와 다른 패턴이 감지되었습니다." 원인 단정 금지.
8. **CI fixture**: 정상 2 + 고장 2 짧은 run과 사전 학습된 소형 IF 모델을 `tests/fixtures/`에 커밋(수 MB 이하). `evals/anomaly-cases.yaml`에 기대 최소 성능(예: JOINT_LAG severity 0.9 탐지, 정상 run 오경보율 < 5%).

## 테스트
- 특징량 계산 결정성, 창 경계.
- 분할에 run 중복 없음(누수 테스트).
- fixture 기반 `evals/anomaly-cases.yaml` 통과.
- `ai` 패키지와 `backend/app/anomaly`가 safety 모듈에 쓰기 의존이 없음.

## 완료 조건
ruff·pytest 통과. 데이터셋 생성 → 학습 → 평가를 실제 실행하고 `reports/eval-v1.md` 핵심 표(모델별 F1·FPR·탐지 지연, 유형별 recall)를 보고. 서버에서 JOINT_LAG fault 주입 시 anomaly_state가 바뀌는 것을 스모크 스크립트로 확인. decision-log 추가.
