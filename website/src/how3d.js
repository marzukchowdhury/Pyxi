/**
 * "How it works" as a real-time 3D scene (three.js), driven by scroll:
 *   1. your screen: a glass display floating in the dark, showing a real Pyxi capture;
 *   2. Pyxi looks: the firefly sweeps a beam of light across it;
 *   3. Pyxi reads: every control lifts off the screen as a glowing glass tile with its name
 *      (the exact boxes of the real window, the way Pyxi's own exploded view works);
 *   4. Pyxi draws: a gold thread grows from the firefly to the Export tile and a ring pulses on it.
 * The camera turns around it as you go, then settles. Only renders while on screen.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

// The slab is the captured app window itself (x 310..1130, y 241..659 of the 1440 x 900 capture).
const WIN = { x: 310, y: 241, w: 820, h: 418 };
const W = 16;
const H = (W * WIN.h) / WIN.w;
const K = W / WIN.w;
const GOLD = new THREE.Color("#ffc861");
const ICE = new THREE.Color("#9ecbff");
const BG = new THREE.Color("#07080b");

const MARKS = [
  // [x, y, w, h, label, depth]: real boxes from the captured window (1440 x 900); depth = how far up it lifts
  [324, 297, 161, 31, "General", 1.2],
  [325, 334, 160, 28, "Appearance", 1.35],
  [324, 367, 161, 31, "Privacy", 1.2],
  [324, 402, 161, 31, "Advanced", 1.2],
  [533, 338, 104, 22, "Dark mode · checkbox", 1.9],
  [533, 373, 124, 22, "Reduce motion · checkbox", 1.9],
  [530, 409, 76, 33, "Export · button", 2.6],
];
const toWorld = (x, y) => new THREE.Vector3((x - WIN.x) * K - W / 2, H / 2 - (y - WIN.y) * K, 0);

const smooth = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
const span = (p, a, b) => smooth((p - a) / (b - a));
const lerp = (a, b, t) => a + (b - a) * t;

/** A rounded dark pill with text, as a sprite (the tags Pyxi puts on things). */
function label(text, color = "#eaf3ff") {
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d");
  const font = '600 44px "Inter Variable", Inter, "Segoe UI", sans-serif';
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(text).width) + 64;
  c.width = w;
  c.height = 84;
  ctx.font = font;
  ctx.fillStyle = "rgba(14,16,22,0.9)";
  ctx.strokeStyle = "rgba(158,203,255,0.55)";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.roundRect(2, 2, w - 4, 80, 40);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.textBaseline = "middle";
  ctx.fillText(text, 32, 44);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, toneMapped: false }));
  sprite.scale.set((w / 84) * 0.5, 0.5, 1);
  sprite.renderOrder = 10;
  return sprite;
}

/** Pyxi's firefly: dark body, glass wings, a glowing tail that lights its surroundings. */
function firefly() {
  const g = new THREE.Group();
  const body = new THREE.MeshPhysicalMaterial({ color: "#2b2416", roughness: 0.35, metalness: 0.2, clearcoat: 1 });
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.075, 24, 16), body);
  head.position.set(0, 0.2, 0);
  const thorax = new THREE.Mesh(new THREE.SphereGeometry(0.1, 24, 16), body);
  thorax.position.set(0, 0.07, 0);
  const tail = new THREE.Mesh(
    new THREE.SphereGeometry(0.13, 32, 24),
    new THREE.MeshBasicMaterial({ color: new THREE.Color("#ffe08a").multiplyScalar(3.2), toneMapped: false }),
  );
  tail.scale.set(1, 1.25, 1);
  tail.position.set(0, -0.14, 0);
  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(0.42, 24, 16),
    new THREE.MeshBasicMaterial({ color: GOLD, transparent: true, opacity: 0.12, depthWrite: false, toneMapped: false }),
  );
  halo.position.copy(tail.position);
  const wingMat = new THREE.MeshPhysicalMaterial({ color: "#fffaf0", transparent: true, opacity: 0.45, roughness: 0.15, transmission: 0.6, side: THREE.DoubleSide });
  const wingGeo = new THREE.CircleGeometry(0.17, 24);
  wingGeo.scale(1, 0.42, 1);
  const wings = [-1, 1].map((s) => {
    const w = new THREE.Mesh(wingGeo, wingMat);
    w.position.set(0.15 * s, 0.11, 0.02);
    w.rotation.z = 0.5 * s;
    return w;
  });
  const light = new THREE.PointLight(GOLD, 3.2, 5, 1.6);
  light.position.copy(tail.position);
  g.add(head, thorax, tail, halo, ...wings, light);
  g.rotation.z = -0.5;
  g.userData = { wings, halo, light, tail };
  return g;
}

export function startHow3D(stage, track) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  } catch {
    return false; // no WebGL: the flat layers stay
  }
  const small = innerWidth < 760;
  renderer.setPixelRatio(Math.min(devicePixelRatio, small ? 1.5 : 1.75));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.95;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(BG, 1);
  const canvas = renderer.domElement;
  canvas.className = "how3d";
  canvas.setAttribute("aria-hidden", "true");
  stage.append(canvas);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(BG, 26, 46);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.32;
  const camera = new THREE.PerspectiveCamera(32, 16 / 10, 0.1, 100);

  scene.add(new THREE.AmbientLight("#8aa0c8", 0.35));
  const key = new THREE.DirectionalLight("#dfe9ff", 1.2);
  key.position.set(-8, 10, 12);
  scene.add(key);

  // ---- the display: a glass slab with the real capture as its screen
  const world = new THREE.Group();
  scene.add(world);
  const bezel = new THREE.Mesh(
    new RoundedBoxGeometry(W + 0.5, H + 0.5, 0.36, 6, 0.3),
    new THREE.MeshPhysicalMaterial({ color: "#0b0d12", roughness: 0.38, metalness: 0.35, clearcoat: 0.6, clearcoatRoughness: 0.25 }),
  );
  bezel.position.z = -0.24;
  world.add(bezel);
  const screenTex = new THREE.TextureLoader().load("/clips/layer-screen.webp");
  screenTex.colorSpace = THREE.SRGBColorSpace;
  screenTex.anisotropy = renderer.capabilities.getMaxAnisotropy();
  screenTex.repeat.set(WIN.w / 1440, WIN.h / 900);
  screenTex.offset.set(WIN.x / 1440, 1 - (WIN.y + WIN.h) / 900);
  // Kept just under the bloom threshold: only light that's meant to glow (tail, thread, edges) blooms.
  const screenMat = new THREE.MeshBasicMaterial({ map: screenTex, toneMapped: false, color: new THREE.Color(0.76, 0.78, 0.83) });
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(W, H), screenMat);
  screen.position.z = 0.002;
  world.add(screen);
  // A glass sheet over the screen: reflections and a soft sheen as the camera turns.
  const glass = new THREE.Mesh(
    new THREE.PlaneGeometry(W, H),
    new THREE.MeshPhysicalMaterial({ color: "#ffffff", roughness: 0.06, metalness: 0, transparent: true, opacity: 0.05, clearcoat: 1, envMapIntensity: 0.6 }),
  );
  glass.position.z = 0.03;
  world.add(glass);
  // A pool of light under the display.
  const glow = new THREE.Mesh(
    new THREE.CircleGeometry(12, 48),
    new THREE.MeshBasicMaterial({ color: "#7fa8ff", transparent: true, opacity: 0.05, depthWrite: false }),
  );
  glow.rotation.x = -Math.PI / 2;
  glow.position.set(0, -6.4, 0);

  // ---- the scan: a sheet of ice-blue light the firefly sweeps across the screen
  const scan = new THREE.Mesh(
    new THREE.PlaneGeometry(0.06, H),
    new THREE.MeshBasicMaterial({ color: ICE.clone().multiplyScalar(2.2), transparent: true, opacity: 0, toneMapped: false, depthWrite: false }),
  );
  const scanWash = new THREE.Mesh(
    new THREE.PlaneGeometry(1.4, H),
    new THREE.MeshBasicMaterial({ color: ICE.clone().multiplyScalar(0.6), transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending }),
  );
  scan.position.z = scanWash.position.z = 0.05;
  world.add(scan, scanWash);

  // ---- the controls Pyxi reads: glass tiles that lift off the screen, with their names
  const tileGlass = new THREE.MeshPhysicalMaterial({ color: "#cfe3ff", roughness: 0.08, transmission: 0.7, thickness: 0.2, transparent: true, opacity: 0.55, clearcoat: 1, envMapIntensity: 1.2 });
  const edgeMat = new THREE.LineBasicMaterial({ color: ICE.clone().multiplyScalar(2.4), transparent: true, opacity: 0, toneMapped: false });
  const tiles = MARKS.map(([x, y, w, h, text, depth], i) => {
    const center = toWorld(x + w / 2, y + h / 2);
    const geo = new RoundedBoxGeometry(w * K, h * K, 0.06, 3, Math.min(w, h) * K * 0.2);
    const mesh = new THREE.Mesh(geo, tileGlass.clone());
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(w * K, h * K, 0.06)), edgeMat.clone());
    const tag = label(text);
    tag.position.set(w * K * 0.5 + tag.scale.x * 0.5 + 0.08, 0, 0.05);
    const pillar = new THREE.Mesh(
      new THREE.CylinderGeometry(0.008, 0.008, 1, 6),
      new THREE.MeshBasicMaterial({ color: ICE.clone().multiplyScalar(1.6), transparent: true, opacity: 0, toneMapped: false }),
    );
    pillar.rotation.x = Math.PI / 2;
    const g = new THREE.Group();
    g.add(mesh, edges, tag);
    g.position.copy(center);
    world.add(g, pillar);
    return { g, mesh, edges, tag, pillar, center, depth, delay: i * 0.012, isExport: text.startsWith("Export") };
  });
  const exportTile = tiles.find((t) => t.isExport);

  // ---- what Pyxi draws: the firefly, its thread of light and the ring on Export
  const fly = firefly();
  scene.add(fly);
  const ringMat = new THREE.MeshBasicMaterial({ color: ICE.clone().multiplyScalar(2.6), transparent: true, opacity: 0, toneMapped: false, depthWrite: false });
  const rings = [0, 1].map(() => {
    const r = new THREE.Mesh(new THREE.TorusGeometry(0.42, 0.018, 8, 64), ringMat.clone());
    scene.add(r);
    return r;
  });
  const threadMat = new THREE.MeshBasicMaterial({ color: GOLD.clone().multiplyScalar(2.6), toneMapped: false, transparent: true, opacity: 0.95 });
  let thread = null;
  const trail = new THREE.Points(
    new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(16 * 3), 3)),
    new THREE.PointsMaterial({ color: GOLD.clone().multiplyScalar(2), size: 0.08, transparent: true, opacity: 0.8, toneMapped: false, depthWrite: false }),
  );
  scene.add(trail);
  const history = [];

  // ---- post: bloom makes the tail, the thread and the edges glow
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(512, 320), 0.6, 0.32, 0.97);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  const resize = () => {
    const r = canvas.getBoundingClientRect();
    const w = Math.max(1, r.width);
    const h = Math.max(1, r.height);
    renderer.setSize(w, h, false);
    composer.setSize(w, h);
    bloom.setSize(w * 0.5, h * 0.5);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(canvas);
  resize();

  // ---- the story, from scroll progress
  let progress = 0;
  const readProgress = () => {
    const r = track.getBoundingClientRect();
    progress = Math.min(1, Math.max(0, -r.top / (r.height - innerHeight)));
  };
  const flyPath = new THREE.CatmullRomCurve3([
    new THREE.Vector3(11, 5, 5),
    new THREE.Vector3(7.8, 4.6, 2.6),
    new THREE.Vector3(-8.6, 3.6, 1.4), // the scan starts at the left edge
    new THREE.Vector3(8.6, 3.6, 1.4), // ...and sweeps to the right
    new THREE.Vector3(3.2, 2.9, 4.2),
  ]);
  const camAt = (p) => {
    // front, then a three-quarter view while the layers come apart, then back to front
    const turn = span(p, 0.3, 0.55) * (1 - span(p, 0.9, 1));
    const az = lerp(0, -0.5, turn);
    const el = lerp(0.05, 0.34, turn);
    const dist = lerp(33, 30, turn);
    return new THREE.Vector3(Math.sin(az) * Math.cos(el) * dist, Math.sin(el) * dist + 0.4, Math.cos(az) * Math.cos(el) * dist);
  };

  const v = new THREE.Vector3();
  let visible = true;
  let raf = 0;
  const clock = new THREE.Clock();
  const frame = () => {
    raf = 0;
    if (!visible) return;
    const t = clock.getElapsedTime();
    const p = progress;
    camera.position.lerp(camAt(p), 0.12);
    camera.lookAt(0, 0, 0.8);

    // 1 -> 2: the firefly flies in and sweeps its beam across the screen
    const flyIn = span(p, 0.02, 0.16);
    const sweep = span(p, 0.18, 0.4);
    const toHover = span(p, 0.62, 0.76);
    const along = flyIn * 0.38 + sweep * 0.38 + toHover * 0.24;
    flyPath.getPointAt(Math.min(0.999, along), v);
    v.y += Math.sin(t * 2.1) * 0.06;
    v.x += Math.sin(t * 1.3) * 0.04;
    fly.position.copy(v);
    fly.userData.wings.forEach((w, i) => (w.rotation.y = Math.sin(t * 42 + i * Math.PI) * 0.9));
    fly.userData.halo.material.opacity = 0.1 + Math.sin(t * 2.4) * 0.04;
    const scanOn = sweep > 0 && sweep < 1 ? 1 : 0;
    const sx = lerp(-W / 2, W / 2, sweep);
    scan.position.x = scanWash.position.x = sx;
    scan.material.opacity = lerp(scan.material.opacity, scanOn * 0.95, 0.2);
    scanWash.material.opacity = lerp(scanWash.material.opacity, scanOn * 0.16, 0.2);
    history.unshift(fly.position.clone());
    if (history.length > 16) history.length = 16;
    const pos = trail.geometry.attributes.position;
    history.forEach((h, i) => pos.setXYZ(i, h.x, h.y, h.z));
    pos.needsUpdate = true;

    // 3: the controls lift off as glass tiles, each to its depth, names alongside
    const lift = span(p, 0.4, 0.6) * (1 - span(p, 0.9, 0.99));
    for (const tile of tiles) {
      const k = smooth((lift - tile.delay * 4) / (1 - tile.delay * 4));
      tile.g.position.z = 0.06 + k * tile.depth;
      tile.mesh.material.opacity = 0.18 + k * 0.4;
      tile.edges.material.opacity = 0.25 + k * 0.75;
      tile.tag.material.opacity = k;
      tile.pillar.material.opacity = k * 0.5;
      tile.pillar.scale.y = Math.max(0.001, tile.g.position.z);
      tile.pillar.position.set(tile.center.x - (MARKS[tiles.indexOf(tile)][2] * K) / 2, tile.center.y, tile.g.position.z / 2);
    }

    // 4: the thread grows from the firefly to Export, and the ring pulses there
    const draw = span(p, 0.7, 0.84) * (1 - span(p, 0.94, 1));
    const target = exportTile.g.position.clone();
    if (thread) {
      scene.remove(thread);
      thread.geometry.dispose();
    }
    if (draw > 0.01) {
      const from = fly.position.clone().add(new THREE.Vector3(0, -0.14, 0));
      const mid = from.clone().lerp(target, 0.5).add(new THREE.Vector3(0, 1.4, 1.2));
      const curve = new THREE.QuadraticBezierCurve3(from, mid, target);
      const pts = curve.getPoints(48).slice(0, Math.max(2, Math.round(48 * draw)));
      thread = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 48, 0.022, 8, false), threadMat);
      scene.add(thread);
    } else thread = null;
    rings.forEach((r, i) => {
      const cyc = (t * 0.7 + i * 0.5) % 1;
      r.position.copy(target).add(new THREE.Vector3(0, 0, 0.05));
      r.scale.setScalar(0.6 + cyc * 1.8);
      r.material.opacity = draw * (1 - cyc) * 0.9;
    });
    exportTile.edges.material.color.copy(ICE).lerp(GOLD, draw * 0.6).multiplyScalar(2.4);

    composer.render();
    raf = requestAnimationFrame(frame);
  };
  const kick = () => {
    if (!raf && visible) raf = requestAnimationFrame(frame);
  };
  addEventListener("scroll", readProgress, { passive: true });
  new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    if (visible) {
      readProgress();
      kick();
    }
  }).observe(stage);
  document.fonts?.ready.then(() => tiles.forEach((t) => t.tag.material.map && (t.tag.material.map.needsUpdate = true)));
  readProgress();
  camera.position.copy(camAt(progress));
  kick();
  return true;
}
