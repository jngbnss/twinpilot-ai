# Phase 5 (선택) — ROS 2 Jazzy + Gazebo Harmonic Adapter

---

TwinPilot AI Phase 5를 구현해줘. 목표는 기존 백엔드·AI·대시보드를 **수정 없이** Gazebo 데이터로 구동하는 두 번째 `SimulatorAdapter`/`TelemetrySource`다. 호스트가 Windows이므로 ROS는 Docker(`osrf/ros:jazzy-desktop` 기반 + Gazebo Harmonic)로만 실행한다.

## 구현 범위 (`ros2_ws/src/`)
1. `twinpilot_description`: `sim/twinpilot_sim/params/excavator.yaml`에서 값을 읽는 Xacro. 시각 메시/충돌 박스 분리. `check_urdf` 통과.
2. `twinpilot_gazebo`: 작업 월드 SDF(구역 A/B, 운반물, 금지 구역, 작업자 모형), IMU·contact·카메라 센서, launch 파일. 운반물 attach는 Gazebo DetachableJoint 시스템으로 규칙 기반 구현(D-002 유지).
3. `twinpilot_control`: ros2_control(gz_ros2_control) 설정, joint_state_broadcaster, 속도 컨트롤러.
4. `twinpilot_bridge`: ros_gz_bridge 설정 + rclpy 노드. ROS 토픽(/joint_states, /imu, /contact) → 공통 `TelemetryRecord` 변환(단위 포함, sim time / receive time 분리) → 백엔드 WS 인입 엔드포인트. 백엔드 ControlCommand → Safety 통과분만 컨트롤러 토픽으로.
5. 백엔드에 `GazeboSource` 추가 (`mode: gazebo`).
6. `docker/ros/Dockerfile`, `docker-compose.yml`, headless 실행 옵션.
7. CI: 별도 job에서 colcon build + check_urdf + SDF 검사 + headless 10 s smoke(선택). Gazebo GUI 실행 금지.

## 완료 조건
docker compose로 headless Gazebo 실행 → 대시보드 모드 GAZEBO에서 관절 상태 실시간 표시 → 키보드 조종 반영. 스크립트 시뮬레이터와 Gazebo의 telemetry 필드 집합 일치 테스트. 발견한 문제·버전·라이선스를 `docs/licenses.md`, `docs/decision-log.md`에 기록.
