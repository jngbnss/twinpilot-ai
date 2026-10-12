// three.js scene, ported from frontend/src/components/Scene3D.tsx (no React).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// World frame (x east, y north, z up) -> three.js (x, y up, -z north)
const w = (x, y, z = 0) => new THREE.Vector3(x, z, -y);

const COLORS = {
  body: 0xf2a541, dark: 0x2b2f33, zoneA: 0x4fb3bf, zoneB: 0x5cc28a, forbidden: 0xe5484d, worker: 0xe8c547,
  object: { box: 0xb07d4f, rock: 0x8a8f94, soil_block: 0x7a5a3a },
};

function label(text, color = "#e6edf2") {
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d");
  const fs = 44;
  ctx.font = `700 ${fs}px "Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif`;
  const tw = Math.ceil(ctx.measureText(text).width);
  c.width = tw + 28;
  c.height = fs + 22;
  ctx.font = `700 ${fs}px "Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif`;
  ctx.fillStyle = "rgba(10,15,19,0.75)";
  ctx.beginPath();
  ctx.roundRect(0, 0, c.width, c.height, 14);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.textBaseline = "middle";
  ctx.fillText(text, 14, c.height / 2 + 2);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  const k = 0.022;
  s.scale.set(c.width * k, c.height * k, 1);
  s.renderOrder = 10;
  return s;
}

const std = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.7, metalness: 0.1, ...extra });

export class Scene {
  constructor(container, params, task) {
    this.params = params;
    this.task = task;
    this.view = "orbit";
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.prepend(renderer.domElement);
    this.renderer = renderer;
    this.container = container;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x11181d);
    scene.fog = new THREE.Fog(0x11181d, 45, 90);
    this.scene = scene;
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 300);
    camera.position.set(-6, 18, 20);
    this.camera = camera;
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.maxPolarAngle = Math.PI / 2.1;
    controls.minDistance = 6;
    controls.maxDistance = 70;
    controls.enableDamping = true;
    this.controls = controls;
    this.target = new THREE.Vector3(0, 0, 3);

    scene.add(new THREE.HemisphereLight(0xcfe3ff, 0x20282e, 0.9));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(15, 25, 10);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -25, right: 25, top: 25, bottom: -25, near: 1, far: 80 });
    scene.add(sun);

    // ground
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(80, 80), std(0x20282e, { roughness: 1 }));
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);
    const grid = new THREE.GridHelper(80, 40, 0x34424c, 0x283239);
    grid.position.y = 0.01;
    scene.add(grid);

    const t = task;
    for (const [z, color, name] of [[t.zone_a, COLORS.zoneA, "구역 A"], [t.zone_b, COLORS.zoneB, "구역 B"]]) {
      const disc = new THREE.Mesh(new THREE.CircleGeometry(z.radius, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.12 }));
      disc.rotation.x = -Math.PI / 2;
      disc.position.copy(w(z.x, z.y, 0.015));
      scene.add(disc);
      scene.add(this.ring(z.x, z.y, z.radius, color));
      const l = label(name, color === COLORS.zoneA ? "#7fd3dc" : "#8fe0b0");
      l.position.copy(w(z.x, z.y + z.radius + 0.6, 0.8));
      scene.add(l);
    }
    for (const z of t.forbidden_zones) {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(z.xmax - z.xmin, z.ymax - z.ymin), new THREE.MeshBasicMaterial({ color: COLORS.forbidden, transparent: true, opacity: 0.22 }));
      m.rotation.x = -Math.PI / 2;
      m.position.copy(w((z.xmin + z.xmax) / 2, (z.ymin + z.ymax) / 2, 0.03));
      scene.add(m);
      const l = label("금지 구역", "#ff9b9b");
      l.position.copy(w((z.xmin + z.xmax) / 2, (z.ymin + z.ymax) / 2, 0.9));
      scene.add(l);
    }
    const wk = t.worker;
    const worker = new THREE.Mesh(new THREE.CapsuleGeometry(0.3, 1.1, 4, 12), std(COLORS.worker));
    worker.position.copy(w(wk.x, wk.y, 0.9));
    worker.castShadow = true;
    scene.add(worker);
    this.workerRing = this.ring(wk.x, wk.y, params.safety.worker_min_distance_m, COLORS.worker, 0.12, 0.5);
    scene.add(this.workerRing);
    const wl = label("작업자", "#f1dd83");
    wl.position.copy(w(wk.x, wk.y, 2.5));
    scene.add(wl);

    this.buildExcavator();
    this.objects = new Map();
    for (const o of t.objects) {
      const geo = o.kind === "rock" ? new THREE.DodecahedronGeometry(o.size_m * 0.55) : new THREE.BoxGeometry(o.size_m, o.size_m, o.size_m);
      const m = new THREE.Mesh(geo, std(COLORS.object[o.kind] ?? 0x999999));
      m.castShadow = true;
      scene.add(m);
      this.objects.set(o.object_id, m);
    }

    this.resize();
    new ResizeObserver(() => this.resize()).observe(container);
  }

  ring(x, y, r, color, width = 0.12, opacity = 0.9) {
    const m = new THREE.Mesh(new THREE.RingGeometry(r - width, r, 64), new THREE.MeshBasicMaterial({ color, transparent: true, opacity, side: THREE.DoubleSide }));
    m.rotation.x = -Math.PI / 2;
    m.position.copy(w(x, y, 0.02));
    return m;
  }

  buildExcavator() {
    const p = this.params;
    const lb = p.links.boom.length_m, la = p.links.arm.length_m, lk = p.links.bucket.length_m;
    const root = new THREE.Group();
    const tilt = new THREE.Group();
    root.add(tilt);
    const box = (sx, sy, sz, mat, x, y, z) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mat);
      m.position.set(x, y, z);
      m.castShadow = true;
      m.receiveShadow = true;
      return m;
    };
    const body = std(COLORS.body), dark = std(COLORS.dark);
    for (const s of [-1, 1]) tilt.add(box(4.2, 0.9, 0.7, dark, 0, 0.45, s * 1.2));
    const upper = new THREE.Group();
    upper.position.set(0, 1.0, 0);
    tilt.add(upper);
    upper.add(box(3.4, 1.1, 2.6, body, -0.6, 0.55, 0));
    upper.add(box(1.2, 1.1, 1.0, std(0x3d4a52), 0.4, 1.5, 0.75));
    upper.add(box(0.9, 0.5, 2.4, std(0x59636b), -2.0, 0.85, 0)); // counterweight
    const boom = new THREE.Group();
    boom.position.set(p.boom_pivot.x_m, p.boom_pivot.z_m - 1.0, -0.2);
    upper.add(boom);
    boom.add(box(lb, 0.45, 0.4, body, lb / 2, 0, 0));
    const arm = new THREE.Group();
    arm.position.set(lb, 0, 0);
    boom.add(arm);
    arm.add(box(la, 0.32, 0.32, body, la / 2, 0, 0));
    const bucket = new THREE.Group();
    bucket.position.set(la, 0, 0);
    arm.add(bucket);
    this.bucketMat = std(0x59636b);
    bucket.add(box(lk, 0.5, 1.0, this.bucketMat, lk / 2, 0, 0));
    this.scene.add(root);
    Object.assign(this, { root, tilt, upper, boom, arm, bucket });
  }

  resize() {
    const r = this.container.getBoundingClientRect();
    if (!r.width || !r.height) return;
    this.renderer.setSize(r.width, r.height, false);
    this.camera.aspect = r.width / r.height;
    this.camera.updateProjectionMatrix();
  }

  setView(v) {
    this.view = v;
    this.controls.enabled = v === "orbit";
  }

  update(gt, warnWorker) {
    const q = (j) => gt.joints[j].position;
    this.root.position.copy(w(gt.base_x, gt.base_y));
    this.root.rotation.y = gt.yaw;
    this.tilt.rotation.set(gt.roll, 0, gt.pitch);
    this.upper.rotation.y = q("swing_joint");
    this.boom.rotation.z = q("boom_joint");
    this.arm.rotation.z = q("arm_joint");
    this.bucket.rotation.z = -q("bucket_joint");
    this.bucketMat.color.setHex(gt.payload_id ? 0xc9853a : 0x59636b);
    for (const o of gt.objects) {
      const m = this.objects.get(o.object_id);
      m.position.copy(w(o.x, o.y, o.z));
      m.material.emissive.setHex(o.state === "delivered" ? 0x1f5f3a : 0x000000);
    }
    this.workerRing.material.color.setHex(warnWorker ? 0xef5b5b : COLORS.worker);
    if (this.view === "cab") {
      const h = gt.yaw + q("swing_joint");
      this.camera.position.copy(w(gt.base_x + 0.4 * Math.cos(h), gt.base_y + 0.4 * Math.sin(h), 3.0));
      this.camera.lookAt(w(gt.base_x + 10 * Math.cos(h), gt.base_y + 10 * Math.sin(h), 0.5));
    } else {
      const goal = w(gt.base_x, gt.base_y + 3, 0);
      const delta = goal.clone().sub(this.target).multiplyScalar(0.05);
      this.target.add(delta);
      this.camera.position.add(delta); // follow while keeping the user's orbit offset
      this.controls.target.copy(this.target);
      this.controls.update();
    }
    this.renderer.render(this.scene, this.camera);
  }
}
