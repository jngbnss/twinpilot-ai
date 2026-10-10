import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { SceneConfig, TwinState } from "../types/api";

// World frame (x east, y north, z up) -> three.js (x, y up, -z north)
const w = (x: number, y: number, z = 0): [number, number, number] => [x, z, -y];

const COLORS = {
  body: "#f2a541",
  dark: "#2b2f33",
  zoneA: "#4fb3bf",
  zoneB: "#5cc28a",
  forbidden: "#e5484d",
  worker: "#e8c547",
  object: { box: "#b07d4f", rock: "#8a8f94", soil_block: "#7a5a3a" } as Record<string, string>,
};

function Ground() {
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} receiveShadow>
        <planeGeometry args={[60, 60]} />
        <meshStandardMaterial color="#20282e" />
      </mesh>
      <gridHelper args={[60, 30, "#34424c", "#283239"]} position={[0, 0.01, 0]} />
    </group>
  );
}

function Ring({ x, y, r, color, width = 0.12, opacity = 0.9 }: {
  x: number; y: number; r: number; color: string; width?: number; opacity?: number;
}) {
  return (
    <mesh position={w(x, y, 0.02)} rotation-x={-Math.PI / 2}>
      <ringGeometry args={[r - width, r, 64]} />
      <meshBasicMaterial color={color} transparent opacity={opacity} side={THREE.DoubleSide} />
    </mesh>
  );
}

function Disc({ x, y, r, color }: { x: number; y: number; r: number; color: string }) {
  return (
    <mesh position={w(x, y, 0.015)} rotation-x={-Math.PI / 2}>
      <circleGeometry args={[r, 48]} />
      <meshBasicMaterial color={color} transparent opacity={0.12} />
    </mesh>
  );
}

function Excavator({ state, cfg }: { state: TwinState; cfg: SceneConfig }) {
  const pose = state.vehicle_pose ?? { x: 0, y: -6, yaw: Math.PI / 2, roll: 0, pitch: 0 };
  const j = state.joint_states;
  const q = (name: string, fallback: number) => j?.[name]?.position ?? fallback;
  const { boom: lb, arm: la, bucket: lk } = {
    boom: cfg.machine.links.boom.length_m,
    arm: cfg.machine.links.arm.length_m,
    bucket: cfg.machine.links.bucket.length_m,
  };
  const piv = cfg.machine.boom_pivot;
  const payload = state.task_state?.payload;
  return (
    <group position={w(pose.x, pose.y)} rotation-y={pose.yaw}>
      <group rotation={[pose.roll, 0, pose.pitch]}>
      {/* tracks */}
      {[-1, 1].map((s) => (
        <mesh key={s} position={[0, 0.45, s * 1.2]} castShadow>
          <boxGeometry args={[4.2, 0.9, 0.7]} />
          <meshStandardMaterial color={COLORS.dark} />
        </mesh>
      ))}
      {/* upper structure */}
      <group position={[0, 1.0, 0]} rotation-y={q("swing_joint", 0)}>
        <mesh position={[-0.6, 0.55, 0]} castShadow>
          <boxGeometry args={[3.4, 1.1, 2.6]} />
          <meshStandardMaterial color={COLORS.body} />
        </mesh>
        <mesh position={[0.4, 1.5, 0.75]} castShadow>
          <boxGeometry args={[1.2, 1.1, 1.0]} />
          <meshStandardMaterial color="#3d4a52" />
        </mesh>
        {/* boom pivot (upper frame: +x forward) */}
        <group position={[piv.x_m, piv.z_m - 1.0, -0.2]} rotation-z={q("boom_joint", 0.6)}>
          <mesh position={[lb / 2, 0, 0]} castShadow>
            <boxGeometry args={[lb, 0.45, 0.4]} />
            <meshStandardMaterial color={COLORS.body} />
          </mesh>
          <group position={[lb, 0, 0]} rotation-z={q("arm_joint", -1.6)}>
            <mesh position={[la / 2, 0, 0]} castShadow>
              <boxGeometry args={[la, 0.32, 0.32]} />
              <meshStandardMaterial color={COLORS.body} />
            </mesh>
            <group position={[la, 0, 0]} rotation-z={-q("bucket_joint", 0)}>
              <mesh position={[lk / 2, 0, 0]} castShadow>
                <boxGeometry args={[lk, 0.5, 1.0]} />
                <meshStandardMaterial color={payload ? "#c9853a" : "#59636b"} />
              </mesh>
            </group>
          </group>
        </group>
      </group>
      </group>
    </group>
  );
}

function Objects({ state, cfg }: { state: TwinState; cfg: SceneConfig }) {
  const objs = state.task_state?.objects;
  if (!objs) return null;
  return (
    <group>
      {objs.map((o) => (
        <mesh key={o.object_id} position={w(o.x, o.y, o.z)} castShadow>
          {o.kind === "rock" ? (
            <dodecahedronGeometry args={[o.size_m * 0.55]} />
          ) : (
            <boxGeometry args={[o.size_m, o.size_m, o.size_m]} />
          )}
          <meshStandardMaterial
            color={COLORS.object[o.kind] ?? "#999"}
            emissive={o.state === "delivered" ? "#1f5f3a" : "#000"}
          />
        </mesh>
      ))}
      {cfg.task.forbidden_zones.map((z) => (
        <mesh
          key={z.name}
          position={w((z.xmin + z.xmax) / 2, (z.ymin + z.ymax) / 2, 0.03)}
          rotation-x={-Math.PI / 2}
        >
          <planeGeometry args={[z.xmax - z.xmin, z.ymax - z.ymin]} />
          <meshBasicMaterial color={COLORS.forbidden} transparent opacity={0.22} />
        </mesh>
      ))}
    </group>
  );
}

function Worker({ cfg }: { cfg: SceneConfig }) {
  const wk = cfg.task.worker;
  return (
    <group>
      <mesh position={w(wk.x, wk.y, 0.9)} castShadow>
        <capsuleGeometry args={[0.3, 1.1, 4, 12]} />
        <meshStandardMaterial color={COLORS.worker} />
      </mesh>
      <Ring x={wk.x} y={wk.y} r={cfg.machine.safety.worker_min_distance_m} color={COLORS.worker} opacity={0.5} />
    </group>
  );
}

function CameraRig({ state, view }: { state: TwinState; view: "orbit" | "cab" }) {
  const { camera, gl } = useThree();
  const controls = useMemo(() => new OrbitControls(camera, gl.domElement), [camera, gl]);
  const target = useRef(new THREE.Vector3());
  useEffect(() => {
    camera.position.set(-6, 18, 20);
    controls.maxPolarAngle = Math.PI / 2.1;
    return () => controls.dispose();
  }, [camera, controls]);
  useFrame(() => {
    const p = state.vehicle_pose;
    if (!p) return;
    if (view === "cab") {
      const heading = p.yaw + (state.joint_states?.swing_joint?.position ?? 0);
      const [cx, cy, cz] = w(p.x + 0.4 * Math.cos(heading), p.y + 0.4 * Math.sin(heading), 3.0);
      camera.position.set(cx, cy, cz);
      const [tx, ty, tz] = w(p.x + 10 * Math.cos(heading), p.y + 10 * Math.sin(heading), 0.5);
      camera.lookAt(tx, ty, tz);
      controls.enabled = false;
    } else {
      controls.enabled = true;
      target.current.lerp(new THREE.Vector3(...w(p.x, p.y + 3, 0)), 0.05);
      controls.target.copy(target.current);
      controls.update();
    }
  });
  return null;
}

export function Scene3D({ state, cfg, view }: { state: TwinState; cfg: SceneConfig; view: "orbit" | "cab" }) {
  const t = cfg.task;
  return (
    <Canvas shadows camera={{ fov: 50, near: 0.1, far: 300 }} style={{ height: "100%" }}>
      <color attach="background" args={["#11181d"]} />
      <hemisphereLight args={["#cfe3ff", "#20282e", 0.6]} />
      <directionalLight position={[15, 25, 10]} intensity={1.4} castShadow />
      <Ground />
      <Disc x={t.zone_a.x} y={t.zone_a.y} r={t.zone_a.radius} color={COLORS.zoneA} />
      <Ring x={t.zone_a.x} y={t.zone_a.y} r={t.zone_a.radius} color={COLORS.zoneA} />
      <Disc x={t.zone_b.x} y={t.zone_b.y} r={t.zone_b.radius} color={COLORS.zoneB} />
      <Ring x={t.zone_b.x} y={t.zone_b.y} r={t.zone_b.radius} color={COLORS.zoneB} />
      <Objects state={state} cfg={cfg} />
      <Worker cfg={cfg} />
      <Excavator state={state} cfg={cfg} />
      <CameraRig state={state} view={view} />
    </Canvas>
  );
}
