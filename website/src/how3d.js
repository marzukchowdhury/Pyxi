/**
 * "How it works" as a real-time 3D scene (three.js), driven by scroll. No screenshots: the app
 * window is drawn here in Pyxi's dark style at high resolution, so every word stays sharp at any
 * angle, and the scene floats straight on the page (transparent canvas, no backdrop).
 *
 *   1. your screen: a settings window, a solid dark slab in space;
 *   2. Pyxi looks: the firefly flies in and sweeps a scan of light across it, and each control
 *      it passes is outlined;
 *   3. Pyxi reads: every control pops out of the window as its own tile, leaving a dark socket
 *      and fine struts back to where it was, with its name beside it (an exploded view);
 *   4. Pyxi draws: a gold thread grows from the firefly to Export and rings pulse on it.
 * The camera turns as you go, then everything settles back. Renders only while on screen.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const PX = 128; // canvas pixels per world unit
const CW = 2048;
const CH = 1280;
const W = CW / PX; // 16
const H = CH / PX; // 10
const GOLD = new THREE.Color("#ffc861");
const ICE = new THREE.Color("#9ecbff");
const FONT = '"Inter Variable", Inter, "Segoe UI", sans-serif';

// The controls Pyxi reads: their box on the drawn window (canvas px), name, lift and tag side.
const CONTROLS = [
  { r: [28, 168, 464, 68], name: "General", depth: 1.0, side: -1 },
  { r: [28, 248, 464, 68], name: "Appearance", depth: 1.2, side: -1 },
  { r: [28, 328, 464, 68], name: "Privacy", depth: 1.0, side: -1 },
  { r: [28, 408, 464, 68], name: "Advanced", depth: 1.0, side: -1 },
  { r: [600, 330, 1360, 104], name: "Dark mode · on", depth: 1.75, side: 1 },
  { r: [600, 452, 1360, 104], name: "Reduce motion · off", depth: 1.75, side: 1 },
  { r: [600, 640, 272, 84], name: "Export · button", depth: 2.8, side: 1, target: true },
];
const wx = (x) => x / PX - W / 2;
const wy = (y) => H / 2 - y / PX;
const smooth = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
const span = (p, a, b) => smooth((p - a) / (b - a));
const lerp = (a, b, t) => a + (b - a) * t;

// ------------------------------------------------------------------ the window, drawn
function rr(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}
function text(ctx, s, x, y, font, color, align = "left") {
  ctx.font = font;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.fillText(s, x, y);
}
function drawUI(ctx) {
  const bg = ctx.createLinearGradient(0, 0, 0, CH);
  bg.addColorStop(0, "#151922");
  bg.addColorStop(1, "#0b0d12");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, CW, CH);
  // title bar
  ctx.fillStyle = "#181c25";
  ctx.fillRect(0, 0, CW, 104);
  ctx.fillStyle = "rgba(255,255,255,0.07)";
  ctx.fillRect(0, 104, CW, 2);
  ["#ff6b6b", "#ffc861", "#5fe3b0"].forEach((c, i) => {
    ctx.globalAlpha = 0.8;
    ctx.fillStyle = c;
    ctx.beginPath();
    ctx.arc(54 + i * 42, 52, 12, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.globalAlpha = 1;
  text(ctx, "Settings", 210, 53, `600 34px ${FONT}`, "rgba(232,238,248,0.78)");
  // sidebar
  ctx.fillStyle = "#0e1117";
  ctx.fillRect(0, 106, 520, CH - 106);
  ctx.fillStyle = "rgba(255,255,255,0.06)";
  ctx.fillRect(520, 106, 2, CH - 106);
  ["General", "Appearance", "Privacy", "Advanced"].forEach((t, i) => {
    const y = 168 + i * 80;
    const on = i === 1;
    if (on) {
      ctx.fillStyle = "rgba(158,203,255,0.15)";
      rr(ctx, 28, y, 464, 68, 16);
      ctx.fill();
    }
    ctx.fillStyle = on ? "#9ecbff" : "rgba(232,238,248,0.32)";
    rr(ctx, 58, y + 22, 24, 24, 7);
    ctx.fill();
    text(ctx, t, 106, y + 35, `${on ? 600 : 500} 34px ${FONT}`, on ? "#eaf3ff" : "rgba(232,238,248,0.72)");
  });
  // the page
  text(ctx, "Appearance", 600, 208, `700 62px ${FONT}`, "#f4f7fb");
  text(ctx, "How everything looks on this PC.", 600, 270, `400 32px ${FONT}`, "rgba(232,238,248,0.5)");
  [
    ["Dark mode", "Easier on the eyes at night", true],
    ["Reduce motion", "Fewer animations everywhere", false],
  ].forEach(([t, sub, on], i) => {
    const y = 330 + i * 122;
    ctx.fillStyle = "rgba(255,255,255,0.04)";
    rr(ctx, 600, y, 1360, 104, 22);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 2;
    ctx.stroke();
    text(ctx, t, 642, y + 38, `600 36px ${FONT}`, "#eef2f8");
    text(ctx, sub, 642, y + 76, `400 27px ${FONT}`, "rgba(232,238,248,0.5)");
    const tx = 1822;
    const ty = y + 29;
    ctx.fillStyle = on ? "#5b9dff" : "rgba(255,255,255,0.14)";
    rr(ctx, tx, ty, 96, 46, 23);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.arc(on ? tx + 73 : tx + 23, ty + 23, 18, 0, Math.PI * 2);
    ctx.fill();
  });
  const btn = ctx.createLinearGradient(0, 640, 0, 724);
  btn.addColorStop(0, "#4f8dff");
  btn.addColorStop(1, "#2e66df");
  ctx.fillStyle = btn;
  rr(ctx, 600, 640, 272, 84, 20);
  ctx.fill();
  text(ctx, "Export", 736, 683, `600 36px ${FONT}`, "#ffffff", "center");
  text(ctx, "Saves your theme to one file.", 906, 683, `400 28px ${FONT}`, "rgba(232,238,248,0.45)");
  ctx.fillStyle = "rgba(255,255,255,0.05)";
  [[600, 806, 1120], [600, 856, 880], [600, 906, 1010], [600, 956, 640]].forEach(([x, y, w]) => {
    rr(ctx, x, y, w, 20, 10);
    ctx.fill();
  });
}

// ------------------------------------------------------------------ small parts
function texture(canvas, renderer) {
  const t = new THREE.CanvasTexture(canvas);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = renderer.capabilities.getMaxAnisotropy();
  return t;
}
function glowTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const x = c.getContext("2d");
  const g = x.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.22, "rgba(255,255,255,0.5)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  x.fillStyle = g;
  x.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}
function washTexture() {
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = 4;
  const x = c.getContext("2d");
  const g = x.createLinearGradient(0, 0, 256, 0);
  g.addColorStop(0, "rgba(158,203,255,0.55)");
  g.addColorStop(1, "rgba(158,203,255,0)");
  x.fillStyle = g;
  x.fillRect(0, 0, 256, 4);
  return new THREE.CanvasTexture(c);
}
/** A crisp dark pill with text, as a sprite (the tags Pyxi puts on things). */
function tag(textValue, renderer, color) {
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d");
  const font = `600 50px ${FONT}`;
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(textValue).width) + 76;
  c.width = w;
  c.height = 100;
  ctx.font = font;
  ctx.fillStyle = "rgba(13,15,21,0.94)";
  ctx.strokeStyle = color === "#ffd98c" ? "rgba(255,200,97,0.7)" : "rgba(158,203,255,0.6)";
  ctx.lineWidth = 3;
  rr(ctx, 2, 2, w - 4, 96, 48);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(36, 50, 7, 0, Math.PI * 2);
  ctx.fill();
  text(ctx, textValue, 56, 52, font, "#f2f6fc");
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture(c, renderer), transparent: true, opacity: 0, depthWrite: false, toneMapped: false }));
  s.scale.set((w / 100) * 0.46, 0.46, 1);
  s.renderOrder = 10;
  return s;
}
/** Pyxi's firefly: a dark body, glassy wings and a glowing tail that lights what's near it. */
function firefly(glow) {
  const g = new THREE.Group();
  const body = new THREE.MeshPhysicalMaterial({ color: "#2b2416", roughness: 0.35, metalness: 0.2, clearcoat: 1 });
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.08, 24, 16), body);
  head.position.set(0, 0.22, 0);
  const thorax = new THREE.Mesh(new THREE.SphereGeometry(0.11, 24, 16), body);
  thorax.position.set(0, 0.07, 0);
  const tail = new THREE.Mesh(
    new THREE.SphereGeometry(0.14, 32, 24),
    new THREE.MeshBasicMaterial({ color: new THREE.Color("#ffe8a3"), toneMapped: false }),
  );
  tail.scale.set(1, 1.25, 1);
  tail.position.set(0, -0.15, 0);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color: GOLD, transparent: true, opacity: 0.85, depthWrite: false, toneMapped: false }));
  halo.scale.setScalar(1.5);
  halo.position.copy(tail.position);
  const wingMat = new THREE.MeshPhysicalMaterial({ color: "#fffaf0", transparent: true, opacity: 0.5, roughness: 0.15, side: THREE.DoubleSide });
  const wingGeo = new THREE.CircleGeometry(0.19, 24);
  wingGeo.scale(1, 0.42, 1);
  const wings = [-1, 1].map((s) => {
    const w = new THREE.Mesh(wingGeo, wingMat);
    w.position.set(0.16 * s, 0.12, 0.02);
    w.rotation.z = 0.5 * s;
    return w;
  });
  const light = new THREE.PointLight(GOLD, 6, 7, 1.5);
  light.position.copy(tail.position);
  g.add(head, thorax, tail, halo, ...wings, light);
  g.rotation.z = -0.5;
  g.userData = { wings, halo };
  return g;
}

// ------------------------------------------------------------------ the scene
/** still: reduced motion. One finished frame (the tiles out, the thread drawn), no movement. */
export function startHow3D(stage, track, { still = false } = {}) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  } catch {
    return false; // no WebGL: the flat layers stay
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);
  const canvas = renderer.domElement;
  canvas.className = "how3d";
  canvas.setAttribute("aria-hidden", "true");
  stage.append(canvas);
  build(renderer, canvas, stage, track, still).catch(() => {
    canvas.remove();
    stage.classList.remove("has-3d");
    renderer.dispose();
  });
  return true;
}

async function build(renderer, canvas, stage, track, still) {
  // The window is drawn with Inter; wait for it so the words don't render in a fallback font.
  await Promise.all(["700 62px", "600 36px", "500 34px", "400 32px"].map((f) => document.fonts.load(`${f} ${FONT}`))).catch(() => undefined);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.5;
  const camera = new THREE.PerspectiveCamera(30, 16 / 10, 0.1, 200);
  scene.add(new THREE.AmbientLight("#9fb2d6", 0.55));
  const key = new THREE.DirectionalLight("#e6eeff", 1.2);
  key.position.set(-10, 12, 14);
  scene.add(key);
  const GLOW = glowTexture();

  // ---- the window
  const ui = document.createElement("canvas");
  ui.width = CW;
  ui.height = CH;
  drawUI(ui.getContext("2d"));
  const world = new THREE.Group();
  scene.add(world);
  const slab = new THREE.Mesh(
    new RoundedBoxGeometry(W + 0.4, H + 0.4, 0.42, 6, 0.32),
    new THREE.MeshPhysicalMaterial({ color: "#0a0c11", roughness: 0.3, metalness: 0.6, clearcoat: 1, clearcoatRoughness: 0.18 }),
  );
  slab.position.z = -0.21;
  world.add(slab);
  const face = new THREE.Mesh(new THREE.PlaneGeometry(W, H), new THREE.MeshBasicMaterial({ map: texture(ui, renderer), toneMapped: false }));
  face.position.z = 0.006;
  world.add(face);
  // A hairline of ice light around the screen's edge.
  const rim = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.PlaneGeometry(W + 0.04, H + 0.04)),
    new THREE.LineBasicMaterial({ color: ICE, transparent: true, opacity: 0.35, toneMapped: false }),
  );
  rim.position.z = 0.01;
  world.add(rim);

  // ---- the scan
  const scanLine = new THREE.Mesh(
    new THREE.PlaneGeometry(0.05, H),
    new THREE.MeshBasicMaterial({ color: new THREE.Color("#e2efff"), transparent: true, opacity: 0, toneMapped: false, depthWrite: false }),
  );
  const wash = new THREE.Mesh(
    new THREE.PlaneGeometry(3.2, H),
    new THREE.MeshBasicMaterial({ map: washTexture(), transparent: true, opacity: 0, depthWrite: false, toneMapped: false }),
  );
  scanLine.position.z = 0.12;
  wash.position.z = 0.11;
  world.add(scanLine, wash);

  // ---- the controls: tiles cut from the drawn window, with sockets, struts and tags
  const tileBody = new THREE.MeshPhysicalMaterial({ color: "#0e1118", roughness: 0.3, metalness: 0.5, clearcoat: 1 });
  const tiles = CONTROLS.map((c, i) => {
    const [x, y, w, h] = c.r;
    const ww = w / PX;
    const hh = h / PX;
    const crop = document.createElement("canvas");
    crop.width = w;
    crop.height = h;
    crop.getContext("2d").drawImage(ui, x, y, w, h, 0, 0, w, h);
    const g = new THREE.Group();
    const body = new THREE.Mesh(new RoundedBoxGeometry(ww, hh, 0.08, 3, Math.min(ww, hh) * 0.16), tileBody);
    const front = new THREE.Mesh(new THREE.PlaneGeometry(ww, hh), new THREE.MeshBasicMaterial({ map: texture(crop, renderer), toneMapped: false }));
    front.position.z = 0.042;
    const outline = new THREE.EdgesGeometry(new THREE.PlaneGeometry(ww, hh));
    const edge = new THREE.LineSegments(outline, new THREE.LineBasicMaterial({ color: ICE.clone(), transparent: true, opacity: 0, toneMapped: false }));
    edge.position.z = 0.046;
    g.add(body, front, edge);
    const center = new THREE.Vector3(wx(x + w / 2), wy(y + h / 2), 0);
    g.position.copy(center);
    const hole = new THREE.Mesh(new THREE.PlaneGeometry(ww, hh), new THREE.MeshBasicMaterial({ color: "#020306", transparent: true, opacity: 0, depthWrite: false }));
    hole.position.set(center.x, center.y, 0.009);
    const socket = new THREE.LineSegments(outline, new THREE.LineBasicMaterial({ color: ICE, transparent: true, opacity: 0, toneMapped: false }));
    socket.position.set(center.x, center.y, 0.013);
    const struts = new THREE.LineSegments(
      new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(8 * 3), 3)),
      new THREE.LineBasicMaterial({ color: ICE, transparent: true, opacity: 0, toneMapped: false }),
    );
    const label = tag(c.name, renderer, c.target ? "#ffd98c" : "#9ecbff");
    world.add(g, hole, socket, struts, label);
    return { ...c, i, g, edge, hole, socket, struts, label, center, ww, hh, seen: 0 };
  });
  const exportTile = tiles.find((t) => t.target);

  // ---- Pyxi: the firefly, its thread and the rings on Export
  const fly = firefly(GLOW);
  scene.add(fly);
  const trail = new THREE.Points(
    new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(18 * 3), 3)),
    new THREE.PointsMaterial({ color: GOLD, size: 0.09, transparent: true, opacity: 0.75, toneMapped: false, depthWrite: false }),
  );
  scene.add(trail);
  const threadCore = new THREE.MeshBasicMaterial({ color: new THREE.Color("#ffe2a0"), toneMapped: false });
  const threadGlow = new THREE.MeshBasicMaterial({ color: GOLD, transparent: true, opacity: 0.2, depthWrite: false, toneMapped: false });
  const threads = [];
  const rings = [0, 1].map(() => {
    const r = new THREE.Mesh(
      new THREE.TorusGeometry(0.6, 0.026, 10, 72),
      new THREE.MeshBasicMaterial({ color: new THREE.Color("#cfe4ff"), transparent: true, opacity: 0, toneMapped: false, depthWrite: false }),
    );
    scene.add(r);
    return r;
  });
  const targetGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: GLOW, color: ICE, transparent: true, opacity: 0, depthWrite: false, toneMapped: false }));
  targetGlow.scale.set(5, 2.6, 1);
  scene.add(targetGlow);

  const resize = () => {
    const r = canvas.getBoundingClientRect();
    const w = Math.max(1, r.width);
    const h = Math.max(1, r.height);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(canvas);
  resize();

  // ---- the story, from scroll progress
  let progress = 0;
  const readProgress = () => {
    if (still) {
      progress = 0.8;
      return;
    }
    const r = track.getBoundingClientRect();
    progress = Math.min(1, Math.max(0, -r.top / (r.height - innerHeight)));
  };
  const camAt = (p) => {
    const turn = span(p, 0.3, 0.58) * (1 - span(p, 0.9, 1));
    const az = lerp(-0.28, -0.62, turn);
    const el = lerp(0.12, 0.28, turn);
    // Narrow screens see less sideways: step back so the tags on both sides stay in frame.
    const dist = lerp(28, 26.5, turn) * (camera.aspect < 1.3 ? 1.22 : 1);
    return new THREE.Vector3(Math.sin(az) * Math.cos(el) * dist, Math.sin(el) * dist, Math.cos(az) * Math.cos(el) * dist);
  };
  const ENTRY = new THREE.Vector3(13, 8, 9);
  const S0 = new THREE.Vector3(8.6, 6.1, 1.6);
  const S1 = new THREE.Vector3(-8.6, 6.1, 1.6);
  const HOVER = new THREE.Vector3(1.2, 2.4, 5);
  const v = new THREE.Vector3();
  const history = [];
  let visible = true;
  let raf = 0;
  const clock = new THREE.Clock();

  const frame = () => {
    raf = 0;
    if (!visible) return;
    const t = clock.getElapsedTime();
    const p = progress;
    camera.position.lerp(camAt(p), still ? 1 : 0.1);
    camera.lookAt(-1.1, -0.3, 1);

    // 1 -> 2: the firefly flies in, then sweeps its scan from right to left along the top
    const flyIn = span(p, 0.02, 0.16);
    const sweep = span(p, 0.18, 0.4);
    const hover = span(p, 0.6, 0.74);
    if (hover > 0) {
      v.copy(S1).lerp(HOVER, hover);
      v.z += Math.sin(hover * Math.PI) * 2.4;
    } else if (sweep > 0) v.copy(S0).lerp(S1, sweep);
    else v.copy(ENTRY).lerp(S0, flyIn);
    v.y += Math.sin(t * 2.1) * 0.06;
    v.x += Math.sin(t * 1.3) * 0.04;
    fly.position.copy(v);
    fly.userData.wings.forEach((w, i) => (w.rotation.y = Math.sin(t * 42 + i * Math.PI) * 0.9));
    fly.userData.halo.material.opacity = 0.75 + Math.sin(t * 2.4) * 0.12;
    history.unshift(fly.position.clone().add(new THREE.Vector3(0, -0.15, 0)));
    if (history.length > 18) history.length = 18;
    const tp = trail.geometry.attributes.position;
    history.forEach((h, i) => tp.setXYZ(i, h.x, h.y, h.z));
    tp.needsUpdate = true;

    const lineX = lerp(W / 2, -W / 2, sweep);
    const scanning = sweep > 0 && sweep < 1 ? 1 : 0;
    scanLine.position.x = lineX;
    wash.position.x = lineX + 1.6;
    scanLine.material.opacity = lerp(scanLine.material.opacity, scanning * 0.95, 0.2);
    wash.material.opacity = lerp(wash.material.opacity, scanning, 0.2);

    // 3: the controls pop out, each to its depth, with a socket, struts and its name
    const end = span(p, 0.9, 0.99);
    const lift = span(p, 0.42, 0.62) * (1 - end);
    for (const tile of tiles) {
      const seenNow = p >= 0.4 || (sweep > 0 && lineX < tile.center.x + tile.ww / 2) ? 1 : 0;
      tile.seen = still ? seenNow : lerp(tile.seen, seenNow, 0.18);
      const k = smooth((lift - tile.i * 0.05) / 0.7);
      const z = k * tile.depth;
      tile.g.position.z = z;
      const shown = tile.seen * (1 - end);
      tile.edge.material.opacity = Math.max(shown * 0.5, k);
      tile.hole.material.opacity = k * 0.82;
      tile.socket.material.opacity = Math.max(shown * 0.45, k * 0.75);
      tile.struts.material.opacity = k * 0.4;
      const sp = tile.struts.geometry.attributes.position;
      const hx = tile.ww / 2;
      const hy = tile.hh / 2;
      [[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]].forEach(([dx, dy], n) => {
        sp.setXYZ(n * 2, tile.center.x + dx, tile.center.y + dy, 0.013);
        sp.setXYZ(n * 2 + 1, tile.center.x + dx, tile.center.y + dy, z);
      });
      sp.needsUpdate = true;
      const lw = tile.label.scale.x;
      tile.label.position.set(
        tile.side > 0 ? tile.center.x + hx + lw / 2 + 0.22 : tile.center.x - hx - lw / 2 - 0.22,
        tile.center.y,
        z + 0.06,
      );
      tile.label.material.opacity = k;
    }

    // 4: the thread grows from the firefly to Export, and rings pulse there
    const draw = span(p, 0.68, 0.82) * (1 - span(p, 0.93, 1));
    const target = exportTile.g.position.clone().add(new THREE.Vector3(0, 0, 0.06));
    while (threads.length) {
      const m = threads.pop();
      scene.remove(m);
      m.geometry.dispose();
    }
    if (draw > 0.01) {
      const from = fly.position.clone().add(new THREE.Vector3(0, -0.15, 0));
      const mid = from.clone().lerp(target, 0.5).add(new THREE.Vector3(-0.4, 1.6, 1.4));
      const pts = new THREE.QuadraticBezierCurve3(from, mid, target).getPoints(64);
      const path = new THREE.CatmullRomCurve3(pts.slice(0, Math.max(2, Math.round(64 * draw))));
      for (const [radius, mat] of [[0.03, threadCore], [0.13, threadGlow]]) {
        const m = new THREE.Mesh(new THREE.TubeGeometry(path, 64, radius, 10, false), mat);
        scene.add(m);
        threads.push(m);
      }
    }
    rings.forEach((r, i) => {
      const cyc = (t * 0.7 + i * 0.5) % 1;
      r.position.copy(target);
      r.scale.set((0.7 + cyc * 1.6) * 1.45, 0.7 + cyc * 1.6, 1);
      r.material.opacity = draw * (1 - cyc) * 0.95;
    });
    targetGlow.position.copy(target);
    targetGlow.material.opacity = draw * 0.45;
    exportTile.edge.material.color.copy(ICE).lerp(GOLD, draw);

    renderer.render(scene, camera);
    // Still: draw until the camera has settled, then stop.
    if (still && t > 1.5) return;
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
  readProgress();
  camera.position.copy(camAt(progress));
  kick();
}
