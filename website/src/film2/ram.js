/**
 * The RAM scene of the launch film, as a pure function of time (see film2/ram.html).
 *
 *   A  0.0 to 2.0   big type: the problem
 *   B  2.0 to c     the page floats in; someone asks; Pyxi builds the inside of a PC in 3D over
 *                   the page and shows the three steps as it says them
 *   C  c to end     into the firefly's light, and the name
 *
 * setup(cfg) is given the times that depend on Pyxi's voice (when each step is said, when the
 * scene ends, how loud the voice is on every frame). draw(t) then sets everything for time t.
 * Nothing here reads a clock, and nothing is random: the same t always gives the same picture.
 *
 * The model is built to the accuracy rules in story.js: four upright slots beside the CPU, a
 * stick already in the second slot, the new one going into the fourth, an off-centre notch that
 * meets the slot's ridge, clips that open first and close when the stick is pressed home.
 */
import "@fontsource-variable/inter";
import "@fontsource-variable/fraunces/wght.css";
import "@fontsource-variable/fraunces/wght-italic.css";
import "./ram.css";
import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { STORY } from "./story.js";

const S = STORY.find((s) => s.id === "engine");
const $ = (id) => document.getElementById(id);
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const seg = (t, a, b) => clamp((t - a) / (b - a));
const out3 = (u) => 1 - (1 - u) ** 3;
const io = (u) => (u < 0.5 ? 4 * u * u * u : 1 - (-2 * u + 2) ** 3 / 2);
const back = (u) => 1 + 2.4 * (u - 1) ** 3 + 1.4 * (u - 1) ** 2;
const mix = (a, b, u) => a + (b - a) * u;
const amber = (l) => l.replace(/\[(.+?)\]/g, "<em>$1</em>");

export const T = { a1: 0.25, a2: 1.0, b: 2.0, listen: 3.0, card: 6.0 };
let C = { s1: 6.6, s2: 8.0, s3: 9.4, c: 12, total: 14.75, env: [], phrases: [] };

// ------------------------------------------------------------------ the picture's fixed parts
$("slam1").innerHTML = `<span>${amber(S.problem[0])}</span>`;
$("slam2").innerHTML = `<span>${amber(S.problem[1])}</span>`;
for (const [id, line] of [["slam1", S.problem[0]], ["slam2", S.problem[1]]]) // a long line gets smaller type and stays on one line
  $(id).style.fontSize = `${Math.min(176, Math.round(3000 / line.replace(/[\[\]]/g, "").length))}px`;
const BUG = `<svg viewBox="-24 -24 48 48"><defs>
  <radialGradient id="fft" cx="0.45" cy="0.4" r="0.65"><stop offset="0" stop-color="#fffbea"/><stop offset="0.55" stop-color="#ffe08a"/><stop offset="1" stop-color="#ffb02e"/></radialGradient>
  <radialGradient id="ffh"><stop offset="0" stop-color="#ffe08a" stop-opacity="0.6"/><stop offset="0.45" stop-color="#ffc861" stop-opacity="0.22"/><stop offset="1" stop-color="#ffc861" stop-opacity="0"/></radialGradient></defs>
  <circle id="halo" r="16" fill="url(#ffh)"/><g transform="rotate(-30)">
  <path class="feel" d="M-0.7 -9.2 Q-1.6 -11.6 -3.4 -12.6 M0.7 -9.2 Q1.6 -11.6 3.4 -12.6"/>
  <g transform="rotate(-38 -2.4 -5)"><ellipse class="wing" cx="-2.4" cy="-5" rx="3.8" ry="1.7"/></g>
  <g transform="rotate(38 2.4 -5)"><ellipse class="wing" cx="2.4" cy="-5" rx="3.8" ry="1.7"/></g>
  <ellipse class="bod" cx="0" cy="-5" rx="2.3" ry="2.6"/><circle class="bod" cx="0" cy="-8.3" r="1.6"/>
  <ellipse cx="0" cy="0" rx="3" ry="3.7" fill="url(#fft)"/></g></svg>`;
$("fly").innerHTML = BUG;
document.querySelector("#name i").innerHTML = BUG.replace(/id="(fft|ffh|halo)"/g, "");
const WORDS = S.ask.split(" ");

// ------------------------------------------------------------------ the 3D model
const renderer = new THREE.WebGLRenderer({ canvas: $("gl"), alpha: true, antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(1);
renderer.setSize(1920, 1080, false);
renderer.setClearColor(0, 0);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
const scene = new THREE.Scene();
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.03).texture;
scene.environmentIntensity = 0.75;
const camera = new THREE.PerspectiveCamera(30, 16 / 9, 0.5, 400);
camera.setViewOffset(1920, 1080, -230, 0, 1920, 1080); // the model sits right of centre, clear of the steps card
const key = new THREE.DirectionalLight(0xffffff, 2.6);
key.position.set(14, 30, 18);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, { left: -24, right: 24, top: 24, bottom: -24, near: 5, far: 90 });
key.shadow.bias = -0.0005;
key.shadow.normalBias = 0.03;
const rim = new THREE.DirectionalLight(0x9ecbff, 1.1);
rim.position.set(-20, 12, -16);
scene.add(key, rim, new THREE.AmbientLight(0xffffff, 0.18));
const root = new THREE.Group();
scene.add(root);

const mat = (color, metalness = 0, roughness = 0.5, extra = {}) => new THREE.MeshPhysicalMaterial({ color, metalness, roughness, ...extra });
const M = {
  pcb: mat("#12261d", 0.1, 0.55, { clearcoat: 0.6, clearcoatRoughness: 0.3 }),
  black: mat("#15171c", 0.2, 0.5),
  slot: mat("#1b1d24", 0.1, 0.45, { clearcoat: 0.4 }),
  clip: mat("#e8ebf0", 0, 0.4, { clearcoat: 0.5 }),
  steel: mat("#c9ced6", 1, 0.25),
  alu: mat("#aab1bd", 0.95, 0.38),
  gold: mat("#e0b25a", 1, 0.25),
  dark: mat("#2a2e37", 0.8, 0.4),
  ram: mat("#16402c", 0.1, 0.5, { clearcoat: 0.7 }),
  chip: mat("#101216", 0.3, 0.35, { clearcoat: 0.5 }),
  cap: mat("#3a3f4b", 0.9, 0.3),
  fan: mat("#22252d", 0.1, 0.5),
  label: mat("#f3f1ea", 0, 0.6),
  glow: new THREE.MeshBasicMaterial({ color: "#ffc861", transparent: true, opacity: 0 }),
};
const parts = []; // { obj, born (0..1 order), home: Vector3 }
function add(geo, material, x, y, z, parent = root, order = null) {
  const m = new THREE.Mesh(geo, material);
  m.position.set(x, y, z);
  m.castShadow = m.receiveShadow = true;
  parent.add(m);
  if (order !== null) parts.push({ obj: m, order, home: m.position.clone() });
  return m;
}
const box = (w, h, d, r = 0.06) => new RoundedBoxGeometry(w, h, d, 2, Math.min(r, w / 2.01, h / 2.01, d / 2.01));

// the board, with its traces drawn on
const tex = (() => {
  const c = document.createElement("canvas");
  c.width = c.height = 1024;
  const x = c.getContext("2d");
  x.fillStyle = "#11241b";
  x.fillRect(0, 0, 1024, 1024);
  let seed = 9;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
  x.strokeStyle = "rgba(120, 190, 150, 0.22)";
  x.lineWidth = 2;
  for (let i = 0; i < 150; i++) {
    let px = rnd() * 1024;
    let py = rnd() * 1024;
    x.beginPath();
    x.moveTo(px, py);
    for (let k = 0; k < 4; k++) {
      if (k % 2) px += (rnd() - 0.5) * 300;
      else py += (rnd() - 0.5) * 300;
      x.lineTo(px, py);
    }
    x.stroke();
    x.fillStyle = "rgba(224, 178, 90, 0.5)";
    x.beginPath();
    x.arc(px, py, 4, 0, 7);
    x.fill();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
})();
const board = add(box(30, 0.35, 24, 0.1), new THREE.MeshPhysicalMaterial({ map: tex, metalness: 0.1, roughness: 0.55, clearcoat: 0.6, clearcoatRoughness: 0.3 }), 0, -0.175, 0, root, 0);

// the CPU: socket frame, the chip's metal lid, and a cooler with fins and a fan
const cpu = new THREE.Group();
cpu.position.set(-4, 0, -1.5);
root.add(cpu);
parts.push({ obj: cpu, order: 0.15, home: cpu.position.clone() });
add(box(5.6, 0.5, 5.6, 0.1), M.steel, 0, 0.25, 0, cpu);
add(box(4.2, 0.35, 4.2, 0.12), M.alu, 0, 0.62, 0, cpu);
const cooler = new THREE.Group();
cooler.position.set(-4, 0.8, -1.5);
root.add(cooler);
parts.push({ obj: cooler, order: 0.3, home: cooler.position.clone() });
for (let i = 0; i < 17; i++) add(box(5.4, 3.0, 0.09, 0.02), M.alu, 0, 1.5, -2.4 + i * 0.3, cooler);
add(new THREE.CylinderGeometry(2.5, 2.5, 0.9, 48, 1, true), M.fan, 0, 3.5, 0, cooler);
const fanSpin = new THREE.Group();
fanSpin.position.set(0, 3.5, 0);
cooler.add(fanSpin);
add(new THREE.CylinderGeometry(0.8, 0.8, 0.5, 24), M.black, 0, 0, 0, fanSpin);
for (let i = 0; i < 9; i++) {
  const b = add(box(1.7, 0.06, 0.8, 0.02), M.fan, 0, 0, 0, fanSpin);
  b.position.set(Math.cos((i / 9) * 6.283) * 1.5, 0, Math.sin((i / 9) * 6.283) * 1.5);
  b.rotation.set(0.5, -(i / 9) * 6.283, 0);
}

// four memory slots beside the CPU, upright, each with a ridge off centre and a clip at each end
const SLOT_X = [3.0, 4.05, 5.1, 6.15];
const SLOT_Z = -1.5;
const LEN = 13.6;
const NOTCH = -0.9; // the ridge sits this far from the middle of the slot
const clips = [];
SLOT_X.forEach((x, i) => {
  const g = new THREE.Group();
  g.position.set(x, 0, SLOT_Z);
  root.add(g);
  parts.push({ obj: g, order: 0.42 + i * 0.05, home: g.position.clone() });
  for (const s of [-1, 1]) add(box(0.22, 0.9, LEN, 0.04), M.slot, s * 0.24, 0.45, 0, g);
  add(box(0.7, 0.2, LEN, 0.04), M.slot, 0, 0.1, 0, g);
  add(box(0.24, 0.55, 0.28, 0.03), M.slot, 0, 0.4, NOTCH, g); // the ridge the notch must meet
  const mine = [];
  for (const s of [-1, 1]) {
    const hinge = new THREE.Group();
    hinge.position.set(0, 0.15, s * (LEN / 2 + 0.1));
    g.add(hinge);
    add(box(0.74, 1.7, 0.46, 0.08), M.clip, 0, 0.85, 0, hinge);
    add(box(0.74, 0.3, 0.34, 0.06), M.clip, 0, 1.55, -s * 0.3, hinge); // the lip that grips the stick
    mine.push({ hinge, s });
  }
  clips.push(mine);
});

/** A memory stick: board, eight chips, a label, and gold contacts with the notch cut out. */
function stick() {
  const g = new THREE.Group();
  add(box(0.13, 3.1, 13.3, 0.03), M.ram, 0, 1.75, 0, g);
  for (let i = 0; i < 8; i++) add(box(0.2, 1.25, 1.1, 0.04), M.chip, 0.1, 2.2, -5.4 + i * 1.54, g);
  add(box(0.15, 0.7, 3.4, 0.02), M.label, -0.07, 2.4, 2.6, g);
  const a = 13.3 / 2 + NOTCH - 0.2; // contacts from the near end to the notch
  const b = 13.3 / 2 - NOTCH - 0.2; // and from the notch to the far end
  add(box(0.16, 0.42, a, 0.01), M.gold, 0, 0.41, -13.3 / 2 + a / 2, g);
  add(box(0.16, 0.42, b, 0.01), M.gold, 0, 0.41, 13.3 / 2 - b / 2, g);
  for (const s of [-1, 1]) add(box(0.16, 0.5, 0.3, 0.02), M.chip, 0, 1.5, s * 6.6, g); // where the clips grip
  return g;
}
const SEAT = 0.02; // a seated stick's own zero sits this far above the board
const old = stick(); // already in the second slot from the CPU
old.position.set(SLOT_X[1], SEAT, SLOT_Z);
root.add(old);
parts.push({ obj: old, order: 0.7, home: old.position.clone() });
const fresh = stick(); // the new one, for the fourth slot
fresh.position.set(SLOT_X[3], 6, SLOT_Z);
root.add(fresh);
const notchGlow = add(box(0.5, 0.9, 0.7, 0.1), M.glow, SLOT_X[3], 0.6, SLOT_Z + NOTCH, root);
notchGlow.castShadow = notchGlow.receiveShadow = false;

// the rest of the board, so it reads as a real one
const extras = [
  [box(1.0, 1.6, 9.5, 0.1), M.dark, -11.2, 0.8, -2, 0.5], // heatsink over the power stages
  [box(8.5, 1.6, 1.0, 0.1), M.dark, -5.5, 0.8, -9.6, 0.52],
  [box(2.2, 3.4, 16, 0.12), M.steel, -13.6, 1.7, -3, 0.55], // the ports at the back
  [box(11.5, 0.8, 0.8, 0.05), M.slot, -4.5, 0.4, 5.2, 0.6], // the long slot for a graphics card
  [box(11.5, 0.8, 0.8, 0.05), M.slot, -4.5, 0.4, 9.2, 0.62],
  [box(4.6, 0.7, 4.6, 0.12), M.dark, 5.4, 0.35, 8.2, 0.64], // chipset heatsink
  [box(1.1, 1.4, 5.4, 0.06), M.black, 13.9, 0.7, 2, 0.66], // the wide power plug
  [box(7.4, 0.14, 2.2, 0.03), M.ram, -3.2, 0.3, 7.2, 0.68], // an M.2 drive lying flat
  [new THREE.CylinderGeometry(1.0, 1.0, 0.3, 32), M.steel, 10.6, 0.15, 9.6, 0.69], // the coin battery
];
for (const [geo, m, x, y, z, order] of extras) add(geo, m, x, y, z, root, order);
for (let i = 0; i < 16; i++) {
  const x = i < 9 ? -9.6 + (i % 9) * 0.01 : -8.6 + (i - 9) * 1.05;
  const z = i < 9 ? -6.2 + i * 1.0 : -8.2;
  add(new THREE.CylinderGeometry(0.34, 0.34, 0.95, 20), M.cap, x, 0.47, z, root, 0.72 + i * 0.004);
}
const TOTAL_PARTS = 31;

// tags pinned to parts of the model
const TAGS = [
  { text: "CPU", at: new THREE.Vector3(-4, 4.6, -1.5), dx: -150, dy: -110, c: "#8fc7ff", on: [0.7, 1.6] },
  { text: "Memory slots", at: new THREE.Vector3(4.6, 1.2, -8.4), dx: 80, dy: -150, c: "#7fe0b0", on: [0.9, 1.6] },
  { text: "Clip", at: new THREE.Vector3(SLOT_X[3], 1.6, SLOT_Z + LEN / 2 + 0.2), dx: 130, dy: -40, c: "#ffe36e", step: 1 },
  { text: "Notch", at: new THREE.Vector3(SLOT_X[3], 0.7, SLOT_Z + NOTCH), dx: 150, dy: 60, c: "#ff9bb5", step: 2 },
].map((t) => {
  const el = document.createElement("div");
  el.className = "tag";
  el.style.setProperty("--c", t.c);
  el.textContent = t.text;
  const line = document.createElement("i");
  line.className = "tagline";
  $("tags").append(line, el);
  return { ...t, el, line };
});

// the camera's stops: [where it is, what it looks at]
const VIEWS = {
  wide: [new THREE.Vector3(27, 25, 31), new THREE.Vector3(-1, 0.5, 0)],
  clips: [new THREE.Vector3(27, 15, 15), new THREE.Vector3(5.6, 1.0, 0)],
  notch: [new THREE.Vector3(24, 6.5, 2), new THREE.Vector3(6.1, 1.9, -2)],
  done: [new THREE.Vector3(28, 15, 14), new THREE.Vector3(4.6, 1.0, -1.5)],
};
const v1 = new THREE.Vector3();
const v2 = new THREE.Vector3();

function model(t) {
  const { s1, s2, s3 } = C;
  // it builds: each part drops into place in its turn
  let shown = 0;
  for (const p of parts) {
    const u = seg(t, T.card + p.order * 1.5, T.card + p.order * 1.5 + 0.55);
    if (u > 0) shown++;
    p.obj.visible = u > 0;
    p.obj.position.copy(p.home);
    p.obj.position.y += (1 - out3(u)) * 9;
    p.obj.scale.setScalar(0.6 + 0.4 * back(u));
  }
  board.scale.setScalar(1);
  fanSpin.rotation.y = t * 9;
  // step 1: the clips of the fourth slot open
  const open = io(seg(t, s1, s1 + 0.5)) * (1 - io(seg(t, s3 + 0.4, s3 + 0.6)));
  clips[3].forEach(({ hinge, s }) => (hinge.rotation.x = s * 0.6 * open));
  // step 2: the new stick comes down until its notch meets the ridge; step 3: it is pressed home
  const arrive = out3(seg(t, s1 + 0.25, s1 + 0.9));
  const lower = io(seg(t, s2 - 0.1, s2 + 0.7));
  const press = io(seg(t, s3 + 0.1, s3 + 0.55));
  fresh.visible = arrive > 0;
  fresh.position.y = mix(mix(mix(12, 5.2, arrive), 0.62, lower), SEAT, press);
  fresh.scale.setScalar(0.7 + 0.3 * arrive);
  M.glow.opacity = 0.75 * seg(t, s2, s2 + 0.3) * (1 - seg(t, s3, s3 + 0.4)) * (0.6 + 0.4 * Math.sin(t * 12));
  // the camera
  const legs = [
    [T.card, "wide", "wide"],
    [s1 - 0.25, "wide", "clips"],
    [s2 - 0.2, "clips", "notch"],
    [s3 + 0.5, "notch", "done"],
  ];
  let [from, to, u] = ["wide", "wide", 0];
  for (const [at, a, b] of legs) if (t >= at) [from, to, u] = [a, b, io(seg(t, at, at + 1.15))];
  camera.position.lerpVectors(VIEWS[from][0], VIEWS[to][0], u);
  v1.lerpVectors(VIEWS[from][1], VIEWS[to][1], u);
  // it never sits still: a slow drift the whole time
  camera.position.x += Math.sin(t * 0.5) * 0.5;
  camera.position.y += Math.sin(t * 0.37 + 1) * 0.25;
  camera.lookAt(v1);
  camera.updateMatrixWorld();
  renderer.render(scene, camera);
  // tags
  const step = t >= s3 ? 3 : t >= s2 ? 2 : t >= s1 ? 1 : 0;
  for (const tag of TAGS) {
    v2.copy(tag.at).project(camera);
    const x = (v2.x * 0.5 + 0.5) * 1920;
    const y = (-v2.y * 0.5 + 0.5) * 1080;
    const o = tag.on
      ? seg(t, T.card + tag.on[0], T.card + tag.on[0] + 0.3) * (1 - seg(t, s1 - 0.4, s1 - 0.1))
      : tag.step === 1
        ? seg(t, s1 + 0.1, s1 + 0.4) * (1 - seg(t, s2 - 0.3, s2))
        : seg(t, s2 + 0.1, s2 + 0.4) * (1 - seg(t, s3 + 0.3, s3 + 0.6));
    const tx = x + tag.dx;
    const ty = y + tag.dy;
    tag.el.style.opacity = o;
    tag.el.style.transform = `translate(${tx.toFixed(1)}px, ${ty.toFixed(1)}px) translate(-50%, -50%) scale(${(0.8 + 0.2 * out3(o)).toFixed(3)})`;
    const len = Math.hypot(tx - x, ty - y);
    tag.line.style.opacity = o * 0.9;
    tag.line.style.width = `${len.toFixed(1)}px`;
    tag.line.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) rotate(${Math.atan2(ty - y, tx - x).toFixed(4)}rad)`;
  }
  return { shown: Math.min(TOTAL_PARTS, Math.round((shown / parts.length) * TOTAL_PARTS)), step };
}

// ------------------------------------------------------------------ draw(t)
function slam(el, t, at, until) {
  const u = seg(t, at, at + 0.22);
  const leave = seg(t, until - 0.32, until);
  el.style.opacity = u * (1 - leave);
  el.style.transform = `scale(${(mix(1.28, 1, out3(u)) + leave * leave * 5).toFixed(4)})`;
  el.style.filter = `blur(${(mix(16, 0, out3(u)) + leave * 14).toFixed(2)}px)`;
}

export function draw(t) {
  const { s1, s3, c } = C;
  // the two slow lights
  $("g1").style.transform = `translate(${(-520 + Math.sin(t * 0.21) * 120).toFixed(1)}px, ${(260 + Math.cos(t * 0.17) * 90).toFixed(1)}px)`;
  $("g2").style.transform = `translate(${(900 + Math.cos(t * 0.19) * 140).toFixed(1)}px, ${(-900 + Math.sin(t * 0.23) * 100).toFixed(1)}px)`;
  // A: the type. The first line gives way to the second; the second is flown through.
  slam($("slam1"), t, T.a1, T.a2 + 0.1);
  slam($("slam2"), t, T.a2, T.b + 0.1);
  // B: the world arrives from far away, at an angle, and straightens as Pyxi is asked
  const arrive = out3(seg(t, T.b - 0.15, T.b + 1.1));
  const leave = seg(t, c - 0.45, c);
  const world = $("world");
  world.style.opacity = seg(t, T.b - 0.15, T.b + 0.2) * (1 - seg(t, c - 0.12, c));
  const punch = io(seg(t, T.listen - 0.3, T.listen + 0.4)) * (1 - io(seg(t, T.card - 0.6, T.card + 0.2))); // in close on the ask
  const scale = mix(0.42, 1, arrive) * (1 + 0.34 * punch) * (1 + leave * leave * 3.2);
  const ry = mix(-34, 0, arrive) + Math.sin(t * 0.4) * 0.8;
  const rx = mix(14, 0, arrive) + Math.cos(t * 0.33) * 0.5;
  world.style.transform = `perspective(2400px) translate(${(-150 * punch).toFixed(1)}px, ${(-60 * punch).toFixed(1)}px) scale(${scale.toFixed(4)}) rotateY(${ry.toFixed(3)}deg) rotateX(${rx.toFixed(3)}deg)`;
  // the page steps back when the model builds over it
  const dim = io(seg(t, T.card - 0.2, T.card + 0.5));
  $("win").style.filter = `brightness(${mix(1, 0.32, dim).toFixed(3)}) blur(${(dim * 5).toFixed(2)}px)`;
  // the pointer, the firefly beside it, and the ask
  const px = 1100 + Math.sin(t * 0.8) * 6;
  const py = 560 + Math.cos(t * 0.7) * 5;
  $("ptr").style.transform = `translate(${px}px, ${py}px)`;
  $("ptr").style.opacity = 1 - dim;
  const said = clamp((t - T.listen) / (T.card - 0.9 - T.listen)) * WORDS.length;
  const pill = $("pill");
  pill.style.opacity = seg(t, T.listen - 0.1, T.listen + 0.15) * (1 - seg(t, T.card - 0.35, T.card - 0.1));
  pill.style.transform = `translate(${px - 330}px, ${py + 52}px)`;
  pill.querySelector("span").innerHTML = WORDS.map((w, i) => (i < said ? w : `<i>${w}</i>`)).join(" ");
  pill.querySelectorAll("s").forEach((s, i) => (s.style.height = `${(8 + 20 * Math.abs(Math.sin(t * 9 + i * 1.7)) * (said < WORDS.length ? 1 : 0.2)).toFixed(1)}px`));
  // the firefly: beside the pointer, then up to where it works; it glows with Pyxi's voice
  const go = io(seg(t, T.card - 0.3, T.card + 0.5));
  const fx = mix(px + 44, 640, go);
  const fy = mix(py + 6, 300, go) + Math.sin(t * 2.1) * 5;
  const loud = C.env[Math.round(t * 60)] ?? 0;
  $("fly").style.transform = `translate(${fx.toFixed(1)}px, ${fy.toFixed(1)}px) scale(${(1 + loud * 0.5 + leave * 18).toFixed(3)})`;
  $("fly").style.filter = `drop-shadow(0 0 ${(6 + loud * 14).toFixed(1)}px #ffd27a) drop-shadow(0 0 ${(18 + loud * 46).toFixed(1)}px rgba(255, 190, 80, 0.9))`;
  // the model, its count, the steps card
  $("gl").style.opacity = seg(t, T.card, T.card + 0.1);
  const m = t >= T.card - 0.05 ? model(t) : { shown: 0, step: 0 };
  $("count").style.opacity = seg(t, T.card + 0.1, T.card + 0.3) * (1 - seg(t, s1 - 0.3, s1));
  $("count").textContent = m.shown < TOTAL_PARTS ? `Building it: ${m.shown} of ${TOTAL_PARTS} parts` : `${TOTAL_PARTS} parts`;
  const card = $("card");
  const cu = out3(seg(t, s1 - 0.25, s1 + 0.2));
  card.style.opacity = cu;
  card.style.transform = `translateX(${mix(-60, 0, cu).toFixed(1)}px) scale(${mix(0.94, 1, back(cu)).toFixed(4)})`;
  card.querySelectorAll("p").forEach((p, i) => {
    const on = m.step > i;
    p.classList.toggle("on", on);
    p.style.opacity = on ? 1 : 0.28;
  });
  // captions: the ask in quotes, then what Pyxi says, phrase by phrase
  let text = "";
  let o = 0;
  if (t >= T.listen + 0.2 && t < T.card - 0.1) {
    text = `“${S.ask}”`;
    o = seg(t, T.listen + 0.2, T.listen + 0.45) * (1 - seg(t, T.card - 0.35, T.card - 0.1));
  }
  for (const ph of C.phrases)
    if (t >= ph.at && t < ph.until) {
      text = ph.text;
      o = seg(t, ph.at, ph.at + 0.12) * (1 - seg(t, ph.until - 0.12, ph.until));
    }
  $("cap").textContent = text;
  $("cap").style.opacity = o;
  $("band").style.opacity = Math.max(o, seg(t, T.listen, T.listen + 0.3) * (1 - seg(t, c - 0.4, c)));
  // C: into the light, and the name
  const flash = $("flash");
  flash.style.setProperty("--x", `${fx.toFixed(0)}px`);
  flash.style.setProperty("--y", `${fy.toFixed(0)}px`);
  flash.style.opacity = seg(t, c - 0.3, c) * (1 - seg(t, c, c + 0.5));
  flash.style.transform = `scale(${(1 + seg(t, c - 0.3, c + 0.4) * 3).toFixed(3)})`;
  if (C.flashIn && t < 0.45) {
    // in the full film this scene opens out of the last one's light
    flash.style.opacity = 1 - seg(t, 0, 0.45);
    flash.style.transform = "scale(4)";
  }
  const n = out3(seg(t, c, c + 0.5));
  $("name").style.opacity = seg(t, c, c + 0.25);
  $("name").style.transform = `scale(${mix(1.25, 1, n).toFixed(4)})`;
  $("name").querySelector("em").style.opacity = seg(t, c + 0.5, c + 0.8);
  return { step: m.step, s3 };
}

export function setup(cfg) {
  C = { ...C, ...cfg };
  return { fonts: ['800 176px "Inter Variable"', '600 44px "Inter Variable"', '600 36px "Fraunces Variable"', 'italic 500 84px "Fraunces Variable"'].map((f) => document.fonts.check(f)) };
}

Object.assign(window, { T, setup, draw });
const at = new URLSearchParams(location.search).get("t");
document.fonts.ready.then(() => {
  document.body.dataset.ready = "1";
  if (at !== null) draw(Number(at));
});
