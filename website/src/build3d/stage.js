/**
 * The live 3D stage: plays one of Pyxi's own object scenes (a model built from parts, with
 * motions and narrated steps) the way the desktop app does, in plain three.js.
 *
 * It follows desktop/src/renderer/src/scene/ObjectView.tsx: a model lit like a product shot on a
 * see-through stage, sticky-note tags on thin leaders, a camera that frames what each step is
 * about and swings round to it, a cutaway that turns the casing into a ghost, and parts that fade
 * out of the way when they block the view. Drag to turn it.
 *
 * Extra here: the model arrives part by part (as it does while Pyxi is still writing it), the
 * steps advance on reading time since nothing is spoken, and it only draws while it is on screen.
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  EASE,
  evaluate,
  fromOrbit,
  normalise,
  orbitSpring,
  smoothDamp,
  stepOrbit,
  storyDuration,
  toOrbit,
} from "./motion.js";
import { buildGeometry, buildKit, makeMaterial } from "./parts.js";

const FOV = 32;
const SWING = 0.75; // seconds a camera move takes to mostly settle
const TAGS = ["#ffe36e", "#ff9bb5", "#7fe0b0", "#8fc7ff", "#c5a8ff", "#ffb36b"]; // sticky-note colours
const ICE = new THREE.Color("#9ecbff");
const GHOST = new THREE.Color("#dfe7f2"); // cutaway shells
const VIEWS = {
  iso: [0.82, 0.58, 1.3],
  front: [0, 0.14, 1],
  back: [0, 0.14, -1],
  side: [1, 0.14, 0.02],
  left: [-1, 0.14, 0.02],
  top: [0.001, 1, 0.12],
  below: [0.001, -1, 0.12],
  close: [0.72, 0.5, 1.25],
  wide: [0.82, 0.58, 1.3],
};
const SVG = "http://www.w3.org/2000/svg";

/** A photographer's studio as an environment map, made here (no downloads): a big overhead
 * softbox, two tall strips, a warm kicker, an ice-blue ring behind and a soft floor bounce. */
function studio(renderer) {
  const env = new THREE.Scene();
  const plane = new THREE.PlaneGeometry(1, 1);
  const light = (geo, color, intensity, pos, scale) => {
    const m = new THREE.Mesh(
      geo,
      new THREE.MeshBasicMaterial({
        color: new THREE.Color(color).multiplyScalar(intensity),
        side: THREE.DoubleSide,
        toneMapped: false,
      }),
    );
    m.position.set(...pos);
    m.scale.set(...scale);
    m.lookAt(0, 0, 0);
    env.add(m);
  };
  light(plane, "#ffffff", 2.4, [0, 7, 1], [12, 7, 1]);
  light(plane, "#ffffff", 2.6, [-6.5, 1.5, 2], [1.6, 9, 1]);
  light(plane, "#ffffff", 1.6, [6.5, 1.5, 2], [1.2, 9, 1]);
  light(plane, "#ffe6c4", 0.9, [4, 0.5, 6], [4, 3, 1]);
  light(new THREE.RingGeometry(0.5, 1, 64), "#9ecbff", 2.2, [0, 2, -7], [4.5, 4.5, 4.5]);
  light(plane, "#d8dde6", 0.35, [0, -4, 0], [14, 14, 1]);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const map = pmrem.fromScene(env, 0.02).texture;
  pmrem.dispose();
  env.traverse((o) => o.material?.dispose());
  return map;
}

/** A soft round shadow to sit the model on something. */
function shadowTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const x = c.getContext("2d");
  const g = x.createRadialGradient(128, 128, 0, 128, 128, 128);
  g.addColorStop(0, "rgba(0,0,0,0.75)");
  g.addColorStop(0.35, "rgba(0,0,0,0.42)");
  g.addColorStop(1, "rgba(0,0,0,0)");
  x.fillStyle = g;
  x.fillRect(0, 0, 256, 256);
  return new THREE.CanvasTexture(c);
}

export function createStage(mount, { reduced = false, onBeat, onBuild, onEnd, onTurn } = {}) {
  const renderer = new THREE.WebGLRenderer({
    alpha: true,
    antialias: true,
    powerPreference: "high-performance",
  });
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  const canvas = renderer.domElement;
  mount.append(canvas);
  const lines = document.createElementNS(SVG, "svg");
  lines.setAttribute("class", "b3-lines");
  const tags = document.createElement("div");
  tags.className = "b3-tags";
  mount.append(lines, tags);

  const world = new THREE.Scene();
  world.environment = studio(renderer);
  world.environmentIntensity = 0.95;
  const camera = new THREE.PerspectiveCamera(FOV, 1.6, 0.02, 500);
  camera.position.set(0, 2, 14);
  const ambient = new THREE.AmbientLight(0xffffff, 0.12);
  const hemi = new THREE.HemisphereLight("#eef4ff", "#1a1d24", 0.35);
  const key = new THREE.DirectionalLight(0xffffff, 1.9);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  const fill = new THREE.DirectionalLight("#cfe0ff", 0.55);
  fill.position.set(-6, 3, -4);
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({
      map: shadowTexture(),
      transparent: true,
      depthWrite: false,
      opacity: 0.55,
      toneMapped: false,
    }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.renderOrder = -1;
  const root = new THREE.Group();
  world.add(ambient, hemi, key, key.target, fill, ground, root);

  const controls = new OrbitControls(camera, canvas);
  controls.enablePan = false;
  controls.enableZoom = false; // the wheel belongs to the page
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  controls.rotateSpeed = 0.7;
  canvas.style.touchAction = "pan-y"; // a vertical swipe still scrolls the page

  // ---- state
  let spec = null;
  let style = "studio";
  let order = []; // parts, parents before children
  const registry = new Map(); // id -> { part, group, layers: [{mesh, mat}], edges, center, appear, born, fly }
  const callouts = new Map(); // id -> { el, line, dot, w, h }
  const clock = { story: 0, free: 0, playing: !reduced, limit: 0 };
  let beatIndex = 0;
  let beatTime = 0; // seconds spent on this step
  let userCut = null; // the visitor's own "show the inside", until the step changes
  let ended = false;
  let manual = false; // the visitor is turning it: the camera is theirs until the next step
  let first = true;
  let spring = null;
  const targetVel = [{ v: 0 }, { v: 0 }, { v: 0 }];
  const seen = new THREE.Box3();
  let build = null; // { t, end, shown } while the model is still arriving
  let rest = null; // the finished model at rest: { box, pts, center, radius }
  let width = 1;
  let height = 1;
  let active = false;
  let wakeUntil = 0;
  let raf = 0;
  let last = 0;
  let frameNo = 0;
  let blocking = new Set();
  let low = false; // stepped down once because the device could not keep up
  let judged = 0;
  let slow = 0;
  const fadeOf = new Map();
  const opacityOf = new Map();
  const focusedOf = new Map();
  const ray = new THREE.Raycaster();
  const v = new THREE.Vector3();
  const tmp = new THREE.Vector3();
  const poolA = [];
  const poolB = [];
  const samples = new WeakMap();

  const beat = () => spec.beats[beatIndex];
  const cutaway = () => userCut ?? beat().cutaway ?? spec.cutaway;
  const wake = (ms = 2600) => {
    wakeUntil = performance.now() + ms;
    run();
  };
  controls.addEventListener("start", () => {
    manual = true;
    onTurn?.();
    wake(60000);
  });
  controls.addEventListener("end", () => wake());

  // ---- building and clearing a model
  function clear() {
    for (const r of registry.values()) {
      for (const { mesh, mat } of r.layers) {
        mesh.geometry.dispose();
        mat.dispose();
      }
      if (r.edges) {
        r.edges.geometry.dispose();
        r.edges.material.dispose();
      }
    }
    registry.clear();
    root.clear();
    for (const c of callouts.values()) {
      c.el.remove();
      c.line.remove();
      c.dot.remove();
    }
    callouts.clear();
    fadeOf.clear();
    opacityOf.clear();
    focusedOf.clear();
  }
  function addPart(p) {
    const group = new THREE.Group();
    const casts = p.shape !== "arrow" && p.material !== "emissive" && p.material !== "glass";
    const layers = [];
    let edges = null;
    const kit = p.shape === "kit" ? buildKit(p) : null;
    if (kit) {
      for (const l of kit) {
        const mat = makeMaterial(
          { ...p, shape: "box", material: l.material, color: l.main ? (p.color ?? l.color) : l.color },
          style,
        );
        const mesh = new THREE.Mesh(l.geometry, mat);
        layers.push({ mesh, mat, source: { material: l.material, color: l.main ? (p.color ?? l.color) : l.color } });
      }
    } else {
      const built = buildGeometry(p);
      const mat = makeMaterial(p, style);
      const mesh = new THREE.Mesh(built.geometry, mat);
      mesh.scale.set(...built.scale);
      layers.push({ mesh, mat, source: null });
      edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(built.geometry, 25),
        new THREE.LineBasicMaterial({ color: "#dbe9ff", transparent: true, opacity: 0.5 }),
      );
      edges.scale.set(...built.scale);
      edges.visible = false;
      group.add(edges);
    }
    const box = new THREE.Box3();
    for (const { mesh } of layers) {
      mesh.castShadow = casts;
      mesh.receiveShadow = true;
      mesh.userData.casts = casts;
      mesh.geometry.computeBoundingBox();
      box.union(mesh.geometry.boundingBox);
      group.add(mesh);
    }
    (p.parent ? registry.get(p.parent).group : root).add(group);
    registry.set(p.id, {
      part: p,
      group,
      layers,
      mesh: layers[0].mesh,
      mat: layers[0].mat,
      edges,
      center: box.getCenter(new THREE.Vector3()),
      appear: 1,
      born: 0,
      fly: new THREE.Vector3(),
    });
  }
  function addCallout(p, i) {
    const el = document.createElement("div");
    el.className = "b3-tag";
    el.style.setProperty("--tag", TAGS[i % TAGS.length]);
    el.textContent = p.label;
    if (p.note) el.title = p.note;
    tags.append(el);
    const line = document.createElementNS(SVG, "path");
    line.setAttribute("class", "b3-line");
    const dot = document.createElementNS(SVG, "circle");
    dot.setAttribute("class", "b3-dot");
    dot.setAttribute("r", "3.4");
    lines.append(line, dot);
    callouts.set(p.id, { el, line, dot, w: 0, h: 0 });
  }
  function restyle() {
    const lineColor = style === "blueprint" ? "#9ecbff" : "#dbe9ff";
    for (const r of registry.values()) {
      for (const l of r.layers) {
        l.mat.dispose();
        l.mat = makeMaterial(
          l.source ? { ...r.part, shape: "box", ...l.source } : r.part,
          style,
        );
        l.mesh.material = l.mat;
      }
      r.mat = r.layers[0].mat;
      r.edges?.material.color.set(lineColor);
    }
    ambient.intensity = style === "blueprint" ? 1.4 : 0.12;
    hemi.intensity = style === "blueprint" ? 0 : 0.35;
    ground.visible = style !== "blueprint";
  }

  /** Put every part where the two clocks say it is, and make it look the way the step wants. */
  function pose(dt, t) {
    const states = evaluate(spec, clock.story, clock.free);
    const focus = new Set(beat().focus);
    const cut = cutaway();
    const k = 1 - Math.exp(-dt * 6);
    for (const p of order) {
      const reg = registry.get(p.id);
      const st = states.get(p.id);
      const a = reg.appear;
      reg.group.position.set(...st.position);
      if (a < 1) reg.group.position.addScaledVector(reg.fly, 1 - a);
      reg.group.rotation.set(...st.rotation);
      const grow = 0.72 + 0.28 * a;
      reg.group.scale.set(st.scale[0] * grow, st.scale[1] * grow, st.scale[2] * grow);
      const parentOp = p.parent ? (opacityOf.get(p.parent) ?? 1) : 1;
      const op = Math.max(0, Math.min(1, st.opacity)) * parentOp;
      opacityOf.set(p.id, op);
      const isFocus = focus.has(p.id) || (p.parent ? (focusedOf.get(p.parent) ?? false) : false);
      focusedOf.set(p.id, isFocus);
      const ghost = cut && p.casing; // cutaway: the casing becomes a ghost, its outline still drawn
      const was = fadeOf.get(p.id) ?? 1;
      const f = was + ((blocking.has(p.id) ? 0.08 : 1) - was) * k;
      fadeOf.set(p.id, f);
      for (const { mesh, mat } of reg.layers) {
        const ud = mat.userData;
        const shown = (ghost ? 0.3 : 1) * op * f * ud.factor * a;
        // a see-through shell is pale silver, not its real paint, so its outline is not lost
        ud.baseColor ??= mat.color.clone();
        ud.baseMetal ??= mat.metalness;
        ud.baseGlow ??= mat.emissive.clone();
        mat.color.lerp(ghost ? GHOST : ud.baseColor, k);
        mat.metalness += ((ghost ? 0.05 : ud.baseMetal) - mat.metalness) * k;
        mat.opacity = shown;
        const transparent = shown < 0.995;
        if (mat.transparent !== transparent) {
          mat.transparent = transparent;
          mat.needsUpdate = true;
        }
        mat.depthWrite = !transparent || shown > 0.6;
        mesh.castShadow = mesh.userData.casts && shown > 0.6;
        mesh.visible = shown > 0.004 && p.shape !== "marker";
        // focus: a quiet ice-blue glow that breathes; pulses brighten glowing parts
        if (p.material === "emissive")
          mat.emissiveIntensity = ud.baseEmissive * (st.glow ? 0.55 + 0.9 * st.glow : 1);
        else if (isFocus && op > 0.05) {
          mat.emissive.copy(ICE);
          mat.emissiveIntensity = 0.16 + 0.08 * Math.sin(t * 3.2) + st.glow * 0.4;
        } else if (st.glow) {
          mat.emissive.copy(ICE);
          mat.emissiveIntensity = st.glow * 0.5;
        } else {
          mat.emissive.copy(ud.baseGlow);
          mat.emissiveIntensity = ud.baseEmissive;
        }
      }
      if (reg.edges) {
        reg.edges.visible =
          op * a > 0.05 && (ghost || f < 0.5 || style === "blueprint" || style === "xray");
        reg.edges.material.opacity = (style === "studio" ? 0.5 : 0.85) * a;
      }
    }
  }

  // ---- what the camera looks at
  const present = (r) => r.mesh.visible && r.mat.opacity >= 0.05;
  function expand(ids) {
    const out = new Set(ids);
    let grew = true;
    while (grew) {
      grew = false;
      for (const [id, r] of registry)
        if (!out.has(id) && r.part.parent && out.has(r.part.parent)) {
          out.add(id);
          grew = true;
        }
    }
    return out;
  }
  function boundsOf(ids, out) {
    out.makeEmpty();
    const box = new THREE.Box3();
    for (const [id, r] of registry) {
      if ((ids && !ids.has(id)) || !present(r)) continue;
      for (const { mesh } of r.layers) {
        box.copy(mesh.geometry.boundingBox).applyMatrix4(mesh.matrixWorld);
        out.union(box);
      }
    }
    return out;
  }
  /** Up to ~60 points on each visible part's surface, in world space (for a tight camera fit). */
  function samplePoints(ids, pool) {
    let n = 0;
    for (const [id, r] of registry) {
      if ((ids && !ids.has(id)) || !present(r)) continue;
      for (const { mesh } of r.layers) {
        const g = mesh.geometry;
        let local = samples.get(g);
        if (!local) {
          const pos = g.getAttribute("position");
          const count = Math.min(60, pos.count);
          local = new Float32Array(count * 3);
          for (let i = 0; i < count; i++) {
            const j = Math.floor((i * pos.count) / count);
            local[i * 3] = pos.getX(j);
            local[i * 3 + 1] = pos.getY(j);
            local[i * 3 + 2] = pos.getZ(j);
          }
          samples.set(g, local);
        }
        for (let i = 0; i < local.length; i += 3) {
          const q = pool[n] ?? (pool[n] = new THREE.Vector3());
          q.set(local[i], local[i + 1], local[i + 2]).applyMatrix4(mesh.matrixWorld);
          n++;
        }
      }
    }
    return pool.slice(0, n);
  }
  /** Parts between the camera and what the step is about, so they can fade out of the way. */
  function occluders(focus) {
    const out = new Set();
    if (!focus.size) return out;
    const subject = expand(focus);
    const center = new THREE.Vector3();
    let n = 0;
    for (const id of subject) {
      const r = registry.get(id);
      if (!r || !r.mesh.visible) continue;
      center.add(v.copy(r.center).applyMatrix4(r.mesh.matrixWorld));
      n++;
    }
    if (!n) return out;
    center.divideScalar(n);
    const from = camera.getWorldPosition(new THREE.Vector3());
    const to = center.clone().sub(from);
    const dist = to.length();
    ray.set(from, to.normalize());
    ray.far = dist;
    for (const [id, r] of registry) {
      if (subject.has(id) || !r.mesh.visible) continue;
      for (const { mesh } of r.layers) {
        const hit = mesh.visible ? ray.intersectObject(mesh, false)[0] : undefined;
        if (hit && hit.distance < dist * 0.92) {
          out.add(id);
          break;
        }
      }
    }
    return out;
  }

  /** Frame the step's subject from the step's angle and follow it; hands over when you drag. */
  function rig(dt) {
    const b = beat();
    const focusIds = b.focus.length && b.camera !== "wide" ? new Set(b.focus) : null;
    const ids = focusIds ? expand(focusIds) : null;
    const box = new THREE.Box3();
    // while the model is still arriving the frame is the finished model's, so it holds steady
    const whole = build ? rest.box.clone() : boundsOf(null, new THREE.Box3());
    if (build) box.copy(rest.box);
    else {
      boundsOf(ids, box);
      if (box.isEmpty()) box.copy(whole);
    }
    if (box.isEmpty()) return;
    seen.union(box); // the frame only grows during a step: it covers the whole range of motion
    const framed = seen.clone();
    if (ids && !whole.isEmpty()) {
      const c = framed.getCenter(new THREE.Vector3()).lerp(whole.getCenter(new THREE.Vector3()), 0.3);
      const half = framed
        .getSize(new THREE.Vector3())
        .max(whole.getSize(new THREE.Vector3()).multiplyScalar(b.camera === "close" ? 0.5 : 0.78))
        .multiplyScalar(0.5);
      framed.set(c.clone().sub(half), c.clone().add(half));
    }
    const centre = framed.getCenter(new THREE.Vector3());
    const vfov = (camera.fov * Math.PI) / 180;
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * (width / height));
    const margin = b.camera === "close" ? 1.06 : b.camera === "wide" ? 1.25 : 1.1;
    const span = whole.isEmpty() ? new THREE.Vector3(1, 1, 1) : whole.getSize(new THREE.Vector3());
    let view = VIEWS[b.camera] ?? VIEWS.iso;
    if (b.camera === "side" || b.camera === "left") {
      const sign = b.camera === "side" ? 1 : -1; // a profile: look across the longest axis
      view = span.x >= span.z ? [0, 0.16, sign] : [sign, 0.16, 0.02];
    }
    const dir = new THREE.Vector3(...view).normalize();
    const right = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), dir);
    if (right.lengthSq() < 1e-6) right.set(1, 0, 0);
    right.normalize();
    const up = new THREE.Vector3().crossVectors(dir, right).normalize();
    // room at the top and bottom for the tags
    const tv = Math.tan(vfov / 2) / (margin * 1.2);
    const th = Math.tan(hfov / 2) / (margin * 1.04);
    // fit the real shape, not its bounding box: points on every part's surface
    const pts = build ? rest.pts : samplePoints(ids, poolA);
    const allPts = build ? rest.pts : ids ? samplePoints(null, poolB) : pts;
    const spread = (P) => {
      let [r0, r1, u0, u1] = [Infinity, -Infinity, Infinity, -Infinity];
      for (const q of P) {
        const d = tmp.copy(q).sub(centre);
        const r = d.dot(right);
        const u = d.dot(up);
        r0 = Math.min(r0, r);
        r1 = Math.max(r1, r);
        u0 = Math.min(u0, u);
        u1 = Math.max(u1, u);
      }
      return [r0, r1, u0, u1];
    };
    if (pts.length) {
      const [r0, r1, u0, u1] = spread(ids ? [...pts, ...allPts.filter((_, i) => i % 3 === 0)] : pts);
      if (Number.isFinite(r0))
        centre.addScaledVector(right, (r0 + r1) / 2).addScaledVector(up, (u0 + u1) / 2);
    }
    const fit = (P) => {
      let d = 0.5;
      for (const q of P) {
        const c = tmp.copy(q).sub(centre);
        const depth = c.dot(dir); // toward the camera
        d = Math.max(d, Math.abs(c.dot(right)) / th + depth, Math.abs(c.dot(up)) / tv + depth);
      }
      return d;
    };
    let dist = 0.5;
    if (pts.length) {
      dist = fit(pts);
      // a close-up keeps some of the whole object in frame, so you know where the part lives
      if (ids) dist = Math.max(dist, fit(allPts) * (b.camera === "close" ? 0.5 : 0.74));
    }
    // stay outside the whole model, so a close-up never puts the lens inside a part
    if (!whole.isEmpty()) {
      const out = new THREE.Ray(centre.clone().addScaledVector(dir, 1e4), dir.clone().negate());
      const entry = out.intersectBox(
        whole.clone().expandByScalar(span.length() * 0.04),
        new THREE.Vector3(),
      );
      if (entry) dist = Math.max(dist, entry.distanceTo(centre) + span.length() * 0.05);
    }
    const goalPos = centre.clone().addScaledVector(dir, Math.max(dist, 0.5));
    if (first) {
      const o = toOrbit(dir.x, dir.y, dir.z);
      if (reduced) o.radius = dist;
      else {
        // arrive from further out and a little round to the side, like a product reveal
        o.radius = dist * 1.7;
        o.theta -= 0.7;
        o.phi = Math.max(0.2, o.phi - 0.12);
      }
      camera.position.copy(centre).add(tmp.set(...fromOrbit(o)));
      controls.target.copy(centre);
      first = false;
      spring = null;
    }
    if (manual) spring = null; // pick up from wherever they leave it
    else {
      const step = Math.min(dt, 0.05);
      spring ??= orbitSpring(toOrbit(...camera.position.clone().sub(controls.target).toArray()));
      const tg = controls.target;
      tg.set(
        smoothDamp(tg.x, centre.x, targetVel[0], SWING, step),
        smoothDamp(tg.y, centre.y, targetVel[1], SWING, step),
        smoothDamp(tg.z, centre.z, targetVel[2], SWING, step),
      );
      const g = goalPos.sub(centre);
      const o = stepOrbit(spring, toOrbit(g.x, g.y, g.z), build ? SWING * 2.2 : SWING, step);
      camera.position.copy(tg).add(tmp.set(...fromOrbit(o)));
    }
    controls.update();
  }

  /** Hang a sticky-note tag above or below each labelled part, on a thin leader, none overlapping. */
  function label() {
    const focus = new Set(beat().focus);
    const items = [];
    for (const [id, c] of callouts) {
      const r = registry.get(id);
      v.copy(r.center).applyMatrix4(r.mesh.matrixWorld).project(camera);
      const ax = (v.x * 0.5 + 0.5) * width;
      const ay = (-v.y * 0.5 + 0.5) * height;
      const op =
        r.mesh.visible || r.part.shape === "marker"
          ? Math.min(1, r.mat.opacity / Math.max(0.05, r.mat.userData.factor))
          : 0;
      if (v.z > 1 || op < 0.12 || ax < 0 || ax > width || ay < 0 || ay > height) {
        c.el.style.opacity = c.line.style.opacity = c.dot.style.opacity = "0";
        continue;
      }
      if (!c.w) {
        c.w = c.el.offsetWidth;
        c.h = c.el.offsetHeight;
      }
      items.push({ id, c, ax, ay, op: Math.max(op, 0.75), w: c.w || 90, h: c.h || 28 });
    }
    if (!items.length) return;
    const midY = items.reduce((n, i) => n + i.ay, 0) / items.length;
    const lift = Math.max(34, Math.min(84, height * 0.1));
    for (const above of [true, false]) {
      const row = items.filter((i) => i.ay <= midY === above).sort((a, b) => a.ax - b.ax);
      if (!row.length) continue;
      const xs = row.map((i) => i.ax);
      const wide = row.reduce((n, i) => n + i.w + 10, 0) > width - 16;
      // push apart left to right, then slide the row back inside the stage; a row too wide for
      // a narrow stage is spread over two levels, each kept inside on its own
      for (const level of wide ? [0, 1] : [null]) {
        const at = row.map((_, i) => i).filter((i) => level === null || i % 2 === level);
        if (!at.length) continue;
        const gap = (a, b) => (row[a].w + row[b].w) / 2 + 10;
        for (let n = 1; n < at.length; n++)
          xs[at[n]] = Math.max(xs[at[n]], xs[at[n - 1]] + gap(at[n - 1], at[n]));
        const end = at[at.length - 1];
        const over = xs[end] + row[end].w / 2 - (width - 8);
        if (over > 0) for (const i of at) xs[i] -= over;
        for (let n = at.length - 2; n >= 0; n--)
          xs[at[n]] = Math.min(xs[at[n]], xs[at[n + 1]] - gap(at[n], at[n + 1]));
        const under = 8 - (xs[at[0]] - row[at[0]].w / 2);
        if (under > 0) for (const i of at) xs[i] += under;
      }
      const edge = above ? Math.min(...row.map((r) => r.ay)) : Math.max(...row.map((r) => r.ay));
      row.forEach((item, i) => {
        // crowded rows stagger in height so the leaders don't cross the tags
        const stagger = (wide || row.length > 3) && i % 2 ? item.h + 8 : 0;
        let ty = above ? edge - lift - stagger - item.h : edge + lift + stagger;
        ty = Math.max(top, Math.min(height - item.h - bottom, ty));
        const tx = xs[i] - item.w / 2;
        const o = String(item.op * (focus.size && !focus.has(item.id) ? 0.72 : 1));
        const { el, line, dot } = item.c;
        el.style.opacity = line.style.opacity = dot.style.opacity = o;
        el.style.transform = `translate(${tx.toFixed(1)}px, ${ty.toFixed(1)}px)`;
        el.classList.toggle("on", focus.has(item.id));
        const ly = above ? ty + item.h : ty;
        line.setAttribute(
          "d",
          `M ${item.ax.toFixed(1)} ${item.ay.toFixed(1)} L ${xs[i].toFixed(1)} ${ly.toFixed(1)}`,
        );
        dot.setAttribute("cx", item.ax.toFixed(1));
        dot.setAttribute("cy", item.ay.toFixed(1));
      });
    }
  }
  // tags keep clear of what the page puts at the top and bottom of the stage
  let top = 6;
  let bottom = 6;

  // ---- the frame
  function frame(ms) {
    raf = 0;
    if (!spec || !active) return;
    const dt = Math.min(0.1, last ? (ms - last) / 1000 : 0.016);
    last = ms;
    const t = ms / 1000;
    frameNo++;
    if (build) {
      build.t += dt;
      let shown = 0;
      for (const r of registry.values()) {
        r.appear = EASE.out(Math.min(1, Math.max(0, (build.t - r.born) / 0.6)));
        if (build.t >= r.born) shown++;
      }
      if (shown !== build.shown) onBuild?.((build.shown = shown), order.length);
      if (build.t >= build.end) {
        build = null;
        first = false;
        onBeat?.(beatIndex, spec.beats[beatIndex], false);
      }
    } else if (clock.playing) {
      clock.free += dt;
      clock.story = Math.min(clock.limit, clock.story + dt);
      beatTime += dt;
      const b = beat();
      const reading = Math.max(3.6, b.narration.split(/\s+/).length * 0.36 + 1.6);
      if (clock.story >= clock.limit - 1e-3 && beatTime >= reading) {
        if (beatIndex < spec.beats.length - 1) setBeat(beatIndex + 1, false);
        else if (!ended) {
          ended = true;
          onEnd?.();
        }
      }
    }
    pose(dt, t);
    root.updateMatrixWorld(true);
    if (frameNo % 6 === 0) blocking = build ? new Set() : occluders(new Set(beat().focus));
    rig(dt);
    camera.updateMatrixWorld();
    label();
    renderer.render(world, camera);
    // can this device keep up? If most of two seconds' frames are slow, step down once
    if (!low && dt < 0.25) {
      judged++;
      if (dt > 0.028) slow++;
      if (judged >= 120) {
        if (slow > 70) {
          low = true;
          key.castShadow = false;
          renderer.shadowMap.enabled = false;
          for (const r of registry.values()) for (const l of r.layers) l.mat.needsUpdate = true;
          resize();
        }
        judged = slow = 0;
      }
    }
    run();
  }
  function run() {
    if (raf || !active || !spec) return;
    if (reduced && performance.now() > wakeUntil) {
      last = 0;
      return;
    }
    raf = requestAnimationFrame(frame);
  }

  function resize() {
    width = Math.max(1, mount.clientWidth);
    height = Math.max(1, mount.clientHeight);
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, low ? 1.25 : 2));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    lines.setAttribute("width", width);
    lines.setAttribute("height", height);
    for (const c of callouts.values()) c.w = 0;
    wake();
  }
  const watcher = new ResizeObserver(resize);
  watcher.observe(mount);

  function setBeat(i, user = true) {
    if (!spec) return;
    beatIndex = Math.max(0, Math.min(spec.beats.length - 1, i));
    const b = beat();
    const next = spec.beats[beatIndex + 1];
    clock.limit = next ? next.at : Math.max(storyDuration(spec), b.at);
    // with motion off nothing plays, so a step shows where it ends up rather than where it starts
    if (user) clock.story = reduced ? clock.limit : b.at;
    beatTime = 0;
    userCut = null;
    ended = false;
    manual = false;
    seen.makeEmpty();
    if (user && build) {
      build = null;
      for (const r of registry.values()) r.appear = 1;
      onBuild?.(order.length, order.length);
    }
    onBeat?.(beatIndex, b, user);
    wake();
  }

  /** Play a scene. `assemble`: let it arrive part by part first. */
  function load(data, { assemble = true } = {}) {
    clear();
    spec = normalise(data);
    const kids = new Map();
    for (const p of spec.parts) kids.set(p.parent ?? null, [...(kids.get(p.parent ?? null) ?? []), p]);
    order = [];
    const walk = (id) =>
      (kids.get(id) ?? []).forEach((p) => {
        order.push(p);
        walk(p.id);
      });
    walk(null);
    order.forEach(addPart);
    spec.parts.filter((p) => p.label).forEach(addCallout);
    restyle();
    Object.assign(clock, { story: 0, free: 0, playing: !reduced });
    beatIndex = 0;
    beatTime = 0;
    userCut = null;
    ended = false;
    manual = false;
    first = true;
    spring = null;
    seen.makeEmpty();
    blocking = new Set();
    const next = spec.beats[1];
    clock.limit = next ? next.at : storyDuration(spec);
    // measure the finished model at rest: the lights, the shadow and the opening frame fit it
    build = null;
    pose(10, 0);
    root.updateMatrixWorld(true);
    const box = boundsOf(null, new THREE.Box3());
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const radius = Math.max(0.5, size.length() / 2);
    rest = { box, center, radius, pts: samplePoints(null, []).map((q) => q.clone()) };
    key.position.copy(center).add(new THREE.Vector3(0.45, 1, 0.6).normalize().multiplyScalar(radius * 3));
    key.target.position.copy(center);
    const cam = key.shadow.camera;
    cam.left = cam.bottom = -radius * 1.15;
    cam.right = cam.top = radius * 1.15;
    cam.near = radius * 0.5;
    cam.far = radius * 5.5;
    cam.updateProjectionMatrix();
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = radius * 0.012;
    const foot = Math.max(size.x, size.z) * 1.9;
    ground.scale.set(foot, foot, 1);
    ground.position.set(center.x, box.min.y - 0.01, center.z);
    if (assemble && !reduced) {
      const gap = Math.min(0.17, 3.6 / order.length);
      order.forEach((p, i) => {
        const r = registry.get(p.id);
        r.born = 0.35 + i * gap;
        r.appear = 0;
        // top-level parts fly in from outside, along the line from the model's middle
        if (!p.parent) {
          v.copy(r.center).applyMatrix4(r.mesh.matrixWorld).sub(center);
          if (v.lengthSq() < 1e-4) v.set(0, 1, 0);
          r.fly.copy(v.normalize().multiplyScalar(radius * 0.55));
        } else r.fly.set(0, 0, 0);
      });
      build = { t: 0, end: 0.35 + order.length * gap + 0.7, shown: -1 };
    } else {
      onBuild?.(order.length, order.length);
      onBeat?.(0, spec.beats[0], false);
    }
    last = 0;
    wake();
  }

  return {
    load,
    setBeat,
    /** The visitor's own cutaway; returns what is showing now. */
    toggleInside() {
      userCut = !cutaway();
      wake();
      return userCut;
    },
    inside: () => (spec ? !!cutaway() : false),
    hasCasing: () => !!spec?.parts.some((p) => p.casing),
    setStyle(next) {
      style = next;
      if (spec) restyle();
      wake();
    },
    /** How much of the stage the page covers at the top and bottom (tags stay out of it). */
    setInsets(t, b) {
      top = t;
      bottom = b;
    },
    setActive(on) {
      active = on;
      last = 0;
      if (on) wake();
    },
    beats: () => spec?.beats ?? [],
    dispose() {
      cancelAnimationFrame(raf);
      watcher.disconnect();
      clear();
      controls.dispose();
      renderer.dispose();
    },
  };
}
