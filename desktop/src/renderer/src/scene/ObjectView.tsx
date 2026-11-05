/**
 * Renders an ObjectScene: a model built from parts, lit like a product shot, floating on the
 * desktop with no background. Callouts sit in two tidy columns with leader lines to the parts
 * they name; the camera frames whatever the current beat is about and follows it as it moves;
 * cutaway turns the casing into a ghost so the inside shows. Drag to orbit, click a labelled
 * part to ask about it.
 *
 * Everything per-frame (motions, camera, callouts) is written straight to three.js objects and
 * DOM refs, so playback never re-renders React.
 */
import { ContactShadows, Edges, Environment, Lightformer, OrbitControls, Outlines, RoundedBox } from "@react-three/drei";
import { Canvas, type ThreeEvent, useFrame, useThree } from "@react-three/fiber";
import { type MutableRefObject, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { Beat, ObjectScene, Part } from "../shared/protocol";
import { children, evaluate } from "./motion";
import { buildGeometry, makeMaterial } from "./parts";

export interface Clock {
  story: number;
  free: number;
  playing: boolean;
  /** The story clock may not pass this (it waits here for the narration to finish). */
  limit: number;
}

interface Props {
  scene: ObjectScene;
  clock: MutableRefObject<Clock>;
  beat: Beat;
  beatIndex: number;
  cutaway: boolean;
  width: number;
  height: number;
  onPick?: (part: Part) => void;
}

interface Registered {
  part: Part;
  group: THREE.Group;
  mesh: THREE.Mesh;
  mat: THREE.MeshPhysicalMaterial;
  edges: THREE.Object3D | null;
  center: THREE.Vector3; // local bounding-box centre of the geometry
}

const FOV = 32;
const TAGS = ["#ffe36e", "#ff9bb5", "#7fe0b0", "#8fc7ff", "#c5a8ff", "#ffb36b"]; // sticky-note colours
const ICE = new THREE.Color("#9ecbff");
const GHOST = new THREE.Color("#dfe7f2"); // cutaway shells
const VIEWS: Record<Beat["camera"], [number, number, number]> = {
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

export function ObjectView({ scene, clock, beat, beatIndex, cutaway, width, height, onPick }: Props) {
  const registry = useRef(new Map<string, Registered>());
  const callouts = useRef(new Map<string, { el: HTMLDivElement | null; line: SVGPathElement | null; dot: SVGCircleElement | null }>());
  const labelled = scene.parts.filter((p) => p.label);

  return (
    <div className="object-view" style={{ width, height }}>
      <Canvas
        gl={{ alpha: true, antialias: true, premultipliedAlpha: false }}
        dpr={[1, 2]}
        camera={{ fov: FOV, near: 0.02, far: 500, position: [0, 2, 14] }}
        onCreated={({ gl }) => {
          gl.setClearColor(0x000000, 0);
          gl.toneMapping = THREE.ACESFilmicToneMapping;
          gl.toneMappingExposure = 1.05;
        }}
        style={{ background: "transparent" }}
      >
        <Studio style={scene.style} />
        <Model scene={scene} clock={clock} beat={beat} cutaway={cutaway} registry={registry} onPick={onPick} />
        <Rig registry={registry} beat={beat} beatIndex={beatIndex} sceneId={scene.id} />
        <CalloutDriver registry={registry} callouts={callouts} beat={beat} width={width} height={height} />
        <Shadow registry={registry} sceneId={scene.id} style={scene.style} />
      </Canvas>
      <svg className="callout-lines" width={width} height={height}>
        {labelled.map((p) => (
          <g key={p.id}>
            <path ref={(el) => { slot(callouts.current, p.id).line = el; }} className="callout-line" />
            <circle ref={(el) => { slot(callouts.current, p.id).dot = el; }} r="3.4" className="callout-dot" />
          </g>
        ))}
      </svg>
      {labelled.map((p, i) => (
        <div key={p.id} ref={(el) => { slot(callouts.current, p.id).el = el; }} className="callout" style={{ opacity: 0, ["--tag" as string]: TAGS[i % TAGS.length] }} title={p.note ?? undefined}>
          {p.label}
        </div>
      ))}
    </div>
  );
}

function slot(map: Map<string, { el: HTMLDivElement | null; line: SVGPathElement | null; dot: SVGCircleElement | null }>, id: string) {
  let s = map.get(id);
  if (!s) map.set(id, (s = { el: null, line: null, dot: null }));
  return s;
}

/** Soft studio light, generated locally (no HDR downloads): a key, two strips and a rim. */
function Studio({ style }: { style: ObjectScene["style"] }) {
  return (
    <>
      <ambientLight intensity={style === "blueprint" ? 1.4 : 0.25} />
      <directionalLight position={[4, 8, 6]} intensity={1.6} />
      <directionalLight position={[-6, 3, -4]} intensity={0.5} color="#cfe0ff" />
      <Environment resolution={256} frames={1}>
        <Lightformer form="rect" intensity={2.2} position={[0, 6, 2]} rotation-x={Math.PI / 2} scale={[10, 6, 1]} />
        <Lightformer form="rect" intensity={1.3} position={[-6, 1, 1]} rotation-y={Math.PI / 2} scale={[8, 2, 1]} />
        <Lightformer form="rect" intensity={1.3} position={[6, 1, 1]} rotation-y={-Math.PI / 2} scale={[8, 2, 1]} />
        <Lightformer form="ring" intensity={1.6} color="#bcd7ff" position={[0, 2, -7]} scale={4} />
        <Lightformer form="rect" intensity={0.5} position={[0, -4, 0]} rotation-x={-Math.PI / 2} scale={[10, 10, 1]} />
      </Environment>
    </>
  );
}

function Model({ scene, clock, beat, cutaway, registry, onPick }: {
  scene: ObjectScene;
  clock: MutableRefObject<Clock>;
  beat: Beat;
  cutaway: boolean;
  registry: MutableRefObject<Map<string, Registered>>;
  onPick?: (part: Part) => void;
}) {
  const kids = useMemo(() => children(scene), [scene]);
  const byId = useMemo(() => new Map(scene.parts.map((p) => [p.id, p])), [scene]);
  const order = useMemo(() => {
    const out: Part[] = [];
    const walk = (id: string | null) => (kids.get(id) ?? []).forEach((p) => { out.push(p); walk(p.id); });
    walk(null);
    return out;
  }, [kids]);
  const focus = useMemo(() => new Set(beat.focus), [beat]);
  const opacity = useRef(new Map<string, number>());
  const focused = useRef(new Map<string, boolean>());
  const fade = useRef(new Map<string, number>());
  const { camera } = useThree();
  const ray = useMemo(() => new THREE.Raycaster(), []);

  useFrame((state, dt) => {
    const c = clock.current;
    if (c.playing) {
      const step = Math.min(dt, 0.1);
      c.free += step;
      c.story = Math.min(c.limit, c.story + step);
    }
    const states = evaluate(scene, c.story, c.free);
    const t = state.clock.elapsedTime;
    const blocking = occluders(registry.current, focus, camera, ray);
    const k = 1 - Math.exp(-dt * 6);
    for (const p of order) {
      const reg = registry.current.get(p.id);
      const st = states.get(p.id);
      if (!reg || !st) continue;
      reg.group.position.set(...st.position);
      reg.group.rotation.set(st.rotation[0], st.rotation[1], st.rotation[2]);
      reg.group.scale.set(...st.scale);
      // Flows are alive: arrows glide a little along their direction and breathe.
      if (p.shape === "arrow") {
        const d = arrowDir(p);
        const glide = (0.5 + 0.5 * Math.sin(t * 3.4 + p.position[0])) * 0.14;
        reg.group.position.x += d[0] * glide;
        reg.group.position.y += d[1] * glide;
        reg.group.position.z += d[2] * glide;
      }
      const parentOp = p.parent ? opacity.current.get(p.parent) ?? 1 : 1;
      const op = Math.max(0, Math.min(1, st.opacity)) * parentOp;
      opacity.current.set(p.id, op);
      const isFocus = focus.has(p.id) || (p.parent ? focused.current.get(p.parent) ?? false : false);
      focused.current.set(p.id, isFocus);
      // Cutaway: the casing becomes a ghost with its outline still drawn.
      const ghost = cutaway && p.casing;
      const f = (fade.current.get(p.id) ?? 1) + ((blocking.has(p.id) ? 0.08 : 1) - (fade.current.get(p.id) ?? 1)) * k;
      fade.current.set(p.id, f);
      const shown = (ghost ? 0.3 : 1) * op * f * (reg.mat.userData.factor as number);
      // A see-through shell is pale silver, not its real paint: dark metal at low opacity on a
      // transparent stage reads as a smudge, and the outline of the thing gets lost.
      const ud = reg.mat.userData as { baseColor?: THREE.Color; baseMetal?: number };
      ud.baseColor ??= reg.mat.color.clone();
      ud.baseMetal ??= reg.mat.metalness;
      reg.mat.color.lerp(ghost ? GHOST : ud.baseColor, k);
      reg.mat.metalness += ((ghost ? 0.05 : ud.baseMetal) - reg.mat.metalness) * k;
      reg.mat.opacity = shown;
      const transparent = shown < 0.995;
      if (reg.mat.transparent !== transparent) {
        reg.mat.transparent = transparent;
        reg.mat.needsUpdate = true;
      }
      reg.mat.depthWrite = !transparent || shown > 0.6;
      reg.mesh.visible = shown > 0.004;
      // Ghosts (cutaway casing, or a part faded out of the way) keep their outline.
      if (reg.edges) reg.edges.visible = ((ghost || f < 0.5) && op > 0.05) || ((scene.style === "blueprint" || scene.style === "xray") && op > 0.05);
      // Focus: a quiet ice-blue glow that breathes; pulses brighten emissive parts.
      const base = reg.mat.userData.baseEmissive as number;
      if (p.material === "emissive") reg.mat.emissiveIntensity = base * (st.glow ? 0.55 + 0.9 * st.glow : 1);
      else if (isFocus && op > 0.05) {
        reg.mat.emissive.copy(ICE);
        reg.mat.emissiveIntensity = 0.16 + 0.08 * Math.sin(t * 3.2) + st.glow * 0.4;
      } else {
        reg.mat.emissiveIntensity = st.glow * 0.5;
        if (st.glow) reg.mat.emissive.copy(ICE);
      }
    }
  });

  const render = (p: Part) => (
    <PartNode key={p.id} part={p} style={scene.style} registry={registry} onPick={onPick ? (part) => {
      // Clicks on unlabelled pieces ask about the nearest labelled ancestor.
      let cur: Part | undefined = part;
      while (cur && !cur.label) cur = cur.parent ? byId.get(cur.parent) : undefined;
      onPick(cur ?? part);
    } : undefined}>
      {(kids.get(p.id) ?? []).map(render)}
    </PartNode>
  );
  return <group>{(kids.get(null) ?? []).map(render)}</group>;
}

function arrowDir(p: Part): [number, number, number] {
  const path = p.path;
  if (path && path.length >= 2) {
    const [a, b] = [path[path.length - 2], path[path.length - 1]];
    const d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const n = Math.hypot(d[0], d[1], d[2]) || 1;
    return [d[0] / n, d[1] / n, d[2] / n];
  }
  return p.axis === "x" ? [1, 0, 0] : p.axis === "z" ? [0, 0, 1] : [0, 1, 0];
}

/** Parts that sit between the camera and what the beat is about (so they can fade out of the way). */
function occluders(registry: Map<string, Registered>, focus: Set<string>, camera: THREE.Camera, ray: THREE.Raycaster): Set<string> {
  const out = new Set<string>();
  if (!focus.size) return out;
  const subject = expand(registry, focus);
  const center = new THREE.Vector3();
  let n = 0;
  const v = new THREE.Vector3();
  for (const id of subject) {
    const r = registry.get(id);
    if (!r || !r.mesh.visible) continue;
    center.add(v.copy(r.center).applyMatrix4(r.mesh.matrixWorld));
    n++;
  }
  if (!n) return out;
  center.divideScalar(n);
  const from = camera.getWorldPosition(new THREE.Vector3());
  const toCenter = center.clone().sub(from);
  const dist = toCenter.length();
  ray.set(from, toCenter.normalize());
  ray.far = dist;
  for (const [id, r] of registry) {
    if (subject.has(id) || !r.mesh.visible) continue;
    const hit = ray.intersectObject(r.mesh, false)[0];
    if (hit && hit.distance < dist * 0.92) out.add(id);
  }
  return out;
}

function PartNode({ part, style, registry, onPick, children: kids }: {
  part: Part;
  style: ObjectScene["style"];
  registry: MutableRefObject<Map<string, Registered>>;
  onPick?: (part: Part) => void;
  children: React.ReactNode;
}) {
  const group = useRef<THREE.Group>(null);
  const mesh = useRef<THREE.Mesh>(null);
  const edges = useRef<THREE.Object3D>(null);
  const built = useMemo(() => buildGeometry(part), [part]);
  const mat = useMemo(() => makeMaterial(part, style), [part, style]);
  useEffect(() => {
    built.geometry.computeBoundingBox();
    const center = built.geometry.boundingBox?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3();
    if (group.current && mesh.current) registry.current.set(part.id, { part, group: group.current, mesh: mesh.current, mat, edges: edges.current, center });
    return () => {
      registry.current.delete(part.id);
    };
  }, [part, built, mat, registry]);
  useEffect(() => () => { built.geometry.dispose(); mat.dispose(); }, [built, mat]);

  const click = onPick ? (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); onPick(part); } : undefined;
  const hover = onPick ? (on: boolean) => (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); document.body.style.cursor = on ? "pointer" : ""; } : undefined;
  const lineColor = style === "blueprint" ? "#9ecbff" : "#dbe9ff";
  const rounded = part.shape === "rounded_box";
  const radius = Math.min(...part.size) * 0.2;
  return (
    <group ref={group}>
      {rounded ? (
        <RoundedBox ref={mesh as never} args={part.size} radius={radius} smoothness={3} material={mat} onClick={click} onPointerOver={hover?.(true)} onPointerOut={hover?.(false)}>
          <Edges ref={edges as never} threshold={25} color={lineColor} visible={false} />
          {style === "sticker" && <Outlines thickness={2.5} screenspace color="#ffffff" />}
        </RoundedBox>
      ) : (
        <mesh ref={mesh} geometry={built.geometry} material={mat} scale={built.scale} onClick={click} onPointerOver={hover?.(true)} onPointerOut={hover?.(false)}>
          <Edges ref={edges as never} threshold={25} color={lineColor} visible={false} />
          {style === "sticker" && <Outlines thickness={2.5} screenspace color="#ffffff" />}
        </mesh>
      )}
      {kids}
    </group>
  );
}

/** World-space bounds of the visible parts (or just the focus), recomputed every frame. */
function boundsOf(registry: Map<string, Registered>, ids: Set<string> | null, out: THREE.Box3): THREE.Box3 {
  out.makeEmpty();
  const box = new THREE.Box3();
  for (const [id, r] of registry) {
    if (ids && !ids.has(id)) continue;
    if (!r.mesh.visible || r.mat.opacity < 0.05) continue;
    const g = r.mesh.geometry;
    if (!g.boundingBox) g.computeBoundingBox();
    box.copy(g.boundingBox!).applyMatrix4(r.mesh.matrixWorld);
    out.union(box);
  }
  return out;
}

const SAMPLES = new WeakMap<THREE.BufferGeometry, Float32Array>();

/** Up to ~60 points on each visible part's surface, in world space (for a tight camera fit). */
function samplePoints(registry: Map<string, Registered>, ids: Set<string> | null, pool: THREE.Vector3[]): THREE.Vector3[] {
  // The vectors are reused frame to frame (a model has hundreds of samples; new ones every frame
  // means garbage-collection stutter).
  let n = 0;
  for (const [id, r] of registry) {
    if (ids && !ids.has(id)) continue;
    if (!r.mesh.visible || r.mat.opacity < 0.05) continue;
    const g = r.mesh.geometry;
    let local = SAMPLES.get(g);
    if (!local) {
      const pos = g.getAttribute("position");
      const n = Math.min(60, pos.count);
      local = new Float32Array(n * 3);
      for (let i = 0; i < n; i++) {
        const k = Math.floor((i * pos.count) / n);
        local[i * 3] = pos.getX(k);
        local[i * 3 + 1] = pos.getY(k);
        local[i * 3 + 2] = pos.getZ(k);
      }
      SAMPLES.set(g, local);
    }
    for (let i = 0; i < local.length; i += 3) {
      const v = pool[n] ?? (pool[n] = new THREE.Vector3());
      v.set(local[i], local[i + 1], local[i + 2]).applyMatrix4(r.mesh.matrixWorld);
      n++;
    }
  }
  return pool.slice(0, n);
}

/** Frames the beat's subject from the beat's angle and follows it; hands over when you drag. */
function Rig({ registry, beat, beatIndex, sceneId }: { registry: MutableRefObject<Map<string, Registered>>; beat: Beat; beatIndex: number; sceneId: string }) {
  const controls = useRef<OrbitControlsImpl>(null);
  const { camera, size } = useThree();
  const manual = useRef(false);
  const first = useRef(true);
  const box = useMemo(() => new THREE.Box3(), []);
  // The frame only grows during a beat: it covers the whole range of motion instead of bobbing
  // along with a piston, and widens to keep a rising seat in view.
  const seen = useMemo(() => new THREE.Box3(), []);
  const sphere = useMemo(() => new THREE.Sphere(), []);
  const goal = useMemo(() => ({ pos: new THREE.Vector3(), target: new THREE.Vector3() }), []);
  const tmp = useMemo(() => new THREE.Vector3(), []);
  const poolA = useMemo(() => [] as THREE.Vector3[], []);
  const poolB = useMemo(() => [] as THREE.Vector3[], []);

  useEffect(() => {
    manual.current = false;
    seen.makeEmpty();
  }, [beatIndex, sceneId, seen]);
  useEffect(() => {
    first.current = true;
  }, [sceneId]);
  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    const start = () => (manual.current = true);
    c.addEventListener("start", start);
    return () => c.removeEventListener("start", start);
  }, []);

  useFrame((_, dt) => {
    const c = controls.current;
    if (!c) return;
    const focusIds = beat.focus.length && beat.camera !== "wide" ? new Set(beat.focus) : null;
    // Followed parts may have children; include them in the frame.
    const ids = focusIds ? expand(registry.current, focusIds) : null;
    boundsOf(registry.current, ids, box);
    if (box.isEmpty()) boundsOf(registry.current, null, box);
    if (box.isEmpty()) return;
    seen.union(box);
    // A close-up keeps some of the whole object in frame, so you know where the part lives.
    const all = boundsOf(registry.current, null, new THREE.Box3());
    const framed = seen.clone();
    if (ids && !all.isEmpty()) {
      const c = framed.getCenter(new THREE.Vector3()).lerp(all.getCenter(new THREE.Vector3()), 0.3);
      const half = framed.getSize(new THREE.Vector3()).max(all.getSize(new THREE.Vector3()).multiplyScalar(beat.camera === "close" ? 0.5 : 0.78)).multiplyScalar(0.5);
      framed.set(c.clone().sub(half), c.clone().add(half));
    }
    framed.getBoundingSphere(sphere);
    const persp = camera as THREE.PerspectiveCamera;
    const vfov = (persp.fov * Math.PI) / 180;
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * (size.width / size.height));
    const margin = beat.camera === "close" ? 1.06 : beat.camera === "wide" ? 1.25 : 1.1;
    // "side" means a profile view: look across the object's longest axis, not down it.
    const whole = boundsOf(registry.current, null, new THREE.Box3());
    const span = whole.isEmpty() ? new THREE.Vector3(1, 1, 1) : whole.getSize(new THREE.Vector3());
    let view = VIEWS[beat.camera];
    if (beat.camera === "side" || beat.camera === "left") {
      const sign = beat.camera === "side" ? 1 : -1;
      view = span.x >= span.z ? [0, 0.16, sign] : [sign, 0.16, 0.02];
    }
    const dir = new THREE.Vector3(...view).normalize();
    // Tight fit: the closest distance at which all 8 corners of the box are inside the view.
    const right = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), dir);
    if (right.lengthSq() < 1e-6) right.set(1, 0, 0);
    right.normalize();
    const up = new THREE.Vector3().crossVectors(dir, right).normalize();
    const tv = Math.tan(vfov / 2) / margin;
    const th = Math.tan(hfov / 2) / margin;
    // Fit the real shape, not its bounding box: a jet's box is mostly empty air between the
    // wingtips, and seen at an angle that box is far bigger than the jet. Points sampled from
    // every part's surface give a tight, centred frame for any object.
    const pts = samplePoints(registry.current, ids, poolA);
    const allPts = ids ? samplePoints(registry.current, null, poolB) : pts;
    const centre = sphere.center.clone();
    const spread = (P: THREE.Vector3[]) => {
      let [r0, r1, u0, u1] = [Infinity, -Infinity, Infinity, -Infinity];
      for (const q of P) {
        const d = tmp.copy(q).sub(centre);
        const r = d.dot(right), u = d.dot(up);
        r0 = Math.min(r0, r); r1 = Math.max(r1, r); u0 = Math.min(u0, u); u1 = Math.max(u1, u);
      }
      return [r0, r1, u0, u1];
    };
    if (pts.length) {
      const [r0, r1, u0, u1] = spread(ids ? [...pts, ...allPts.filter((_, i) => i % 3 === 0)] : pts);
      if (Number.isFinite(r0)) centre.addScaledVector(right, (r0 + r1) / 2).addScaledVector(up, (u0 + u1) / 2);
    }
    const fit = (P: THREE.Vector3[]) => {
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
      // A close-up keeps some of the whole object in frame, so you know where the part lives.
      if (ids) dist = Math.max(dist, fit(allPts) * (beat.camera === "close" ? 0.5 : 0.74));
    } else {
      for (let i = 0; i < 8; i++) {
        const corner = new THREE.Vector3(i & 1 ? framed.max.x : framed.min.x, i & 2 ? framed.max.y : framed.min.y, i & 4 ? framed.max.z : framed.min.z).sub(sphere.center);
        const depth = corner.dot(dir);
        dist = Math.max(dist, Math.abs(corner.dot(right)) / th + depth, Math.abs(corner.dot(up)) / tv + depth);
      }
    }
    sphere.center.copy(centre);
    goal.target.copy(sphere.center);
    // Stay outside the whole model, so a close-up never puts the lens inside a part.
    if (!whole.isEmpty()) {
      const out = new THREE.Ray(sphere.center.clone().addScaledVector(dir, 1e4), dir.clone().negate());
      const entry = out.intersectBox(whole.clone().expandByScalar(span.length() * 0.04), new THREE.Vector3());
      if (entry) dist = Math.max(dist, entry.distanceTo(sphere.center) + span.length() * 0.05);
    }
    goal.pos.copy(sphere.center).addScaledVector(dir, Math.max(dist, 0.5));
    if (first.current) {
      // Arrive from a little further out, like the model is settling into place.
      camera.position.copy(sphere.center).addScaledVector(dir, dist * 1.7);
      c.target.copy(sphere.center);
      first.current = false;
    }
    if (!manual.current) {
      const k = 1 - Math.exp(-dt * 2.4);
      camera.position.lerp(goal.pos, k);
      c.target.lerp(goal.target, k);
    }
    c.update();
  });

  return <OrbitControls ref={controls} makeDefault enablePan={false} enableDamping dampingFactor={0.12} rotateSpeed={0.7} zoomSpeed={0.8} />;
}

function expand(registry: Map<string, Registered>, ids: Set<string>): Set<string> {
  const out = new Set(ids);
  let grew = true;
  while (grew) {
    grew = false;
    for (const [id, r] of registry) {
      if (!out.has(id) && r.part.parent && out.has(r.part.parent)) {
        out.add(id);
        grew = true;
      }
    }
  }
  return out;
}

/** Projects labelled parts to the screen and hangs a sticky-note tag above or below each one,
 * on a thin leader, spread so no two tags overlap and none leaves the stage. */
function CalloutDriver({ registry, callouts, beat, width, height }: {
  registry: MutableRefObject<Map<string, Registered>>;
  callouts: MutableRefObject<Map<string, { el: HTMLDivElement | null; line: SVGPathElement | null; dot: SVGCircleElement | null }>>;
  beat: Beat;
  width: number;
  height: number;
}) {
  const { camera } = useThree();
  const v = useMemo(() => new THREE.Vector3(), []);
  const focus = useMemo(() => new Set(beat.focus), [beat]);
  const sizes = useMemo(() => new Map<string, [number, number]>(), []);
  useFrame(() => {
    const items: { id: string; ax: number; ay: number; op: number; w: number; h: number }[] = [];
    for (const [id, slotted] of callouts.current) {
      const r = registry.current.get(id);
      if (!r || !slotted.el) continue;
      v.copy(r.center).applyMatrix4(r.mesh.matrixWorld).project(camera);
      const behind = v.z > 1;
      const ax = (v.x * 0.5 + 0.5) * width;
      const ay = (-v.y * 0.5 + 0.5) * height;
      const op = r.mesh.visible ? Math.min(1, r.mat.opacity / Math.max(0.05, r.mat.userData.factor as number)) : 0;
      // A ghosted casing still gets its tag; only parts that are really gone lose theirs.
      if (behind || op < 0.12 || ax < 0 || ax > width || ay < 0 || ay > height) {
        hide(slotted);
        continue;
      }
      let size = sizes.get(id);
      if (!size || !size[0]) sizes.set(id, (size = [slotted.el.offsetWidth, slotted.el.offsetHeight]));
      items.push({ id, ax, ay, op: Math.max(op, 0.75), w: size[0] || 90, h: size[1] || 28 });
    }
    if (!items.length) return;
    // Tags go above the parts in the top half of the model and below the rest.
    const midY = items.reduce((n, i) => n + i.ay, 0) / items.length;
    const lift = Math.max(46, Math.min(96, height * 0.12));
    for (const above of [true, false]) {
      const row = items.filter((i) => (i.ay <= midY) === above).sort((a, b) => a.ax - b.ax);
      if (!row.length) continue;
      const xs = row.map((i) => i.ax);
      // Push apart left to right, then slide the row back inside the stage.
      for (let i = 1; i < row.length; i++) xs[i] = Math.max(xs[i], xs[i - 1] + (row[i - 1].w + row[i].w) / 2 + 10);
      const over = xs[xs.length - 1] + row[row.length - 1].w / 2 - (width - 8);
      if (over > 0) for (let i = 0; i < xs.length; i++) xs[i] -= over;
      for (let i = xs.length - 2; i >= 0; i--) xs[i] = Math.min(xs[i], xs[i + 1] - (row[i].w + row[i + 1].w) / 2 - 10);
      const under = 8 - (xs[0] - row[0].w / 2);
      if (under > 0) for (let i = 0; i < xs.length; i++) xs[i] += under;
      row.forEach((item, i) => {
        const s = callouts.current.get(item.id)!;
        // Crowded rows stagger in height so the leaders don't cross the tags.
        const stagger = row.length > 3 && i % 2 ? item.h + 8 : 0;
        const edge = above ? Math.min(...row.map((r) => r.ay)) : Math.max(...row.map((r) => r.ay));
        let ty = above ? edge - lift - stagger - item.h : edge + lift + stagger;
        ty = Math.max(6, Math.min(height - item.h - 6, ty));
        const tx = xs[i] - item.w / 2;
        const dim = focus.size && !focus.has(item.id) ? 0.72 : 1;
        s.el!.style.opacity = String(item.op * dim);
        s.el!.style.transform = `translate(${tx.toFixed(1)}px, ${ty.toFixed(1)}px)`;
        s.el!.classList.toggle("on", focus.has(item.id));
        const ly = above ? ty + item.h : ty;
        s.line?.setAttribute("d", `M ${item.ax.toFixed(1)} ${item.ay.toFixed(1)} L ${xs[i].toFixed(1)} ${ly.toFixed(1)}`);
        s.line?.style.setProperty("opacity", String(item.op * dim));
        s.dot?.setAttribute("cx", String(item.ax));
        s.dot?.setAttribute("cy", String(item.ay));
        s.dot?.style.setProperty("opacity", String(item.op * dim));
      });
    }
  });
  return null;
}

function hide(s: { el: HTMLDivElement | null; line: SVGPathElement | null; dot: SVGCircleElement | null }) {
  if (s.el) s.el.style.opacity = "0";
  s.line?.style.setProperty("opacity", "0");
  s.dot?.style.setProperty("opacity", "0");
}

/** A soft contact shadow under where the model starts, so it sits on something. Measured once
 * per model: its size follows the footprint and it only catches what's near the floor. */
function Shadow({ registry, sceneId, style }: { registry: MutableRefObject<Map<string, Registered>>; sceneId: string; style: ObjectScene["style"] }) {
  const [spot, setSpot] = useState<{ id: string; pos: [number, number, number]; scale: number; far: number } | null>(null);
  const box = useMemo(() => new THREE.Box3(), []);
  useFrame(() => {
    if (spot?.id === sceneId) return;
    boundsOf(registry.current, null, box);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    setSpot({ id: sceneId, pos: [center.x, box.min.y - 0.01, center.z], scale: Math.max(size.x, size.z) * 1.4, far: Math.max(0.2, size.y * 0.35) });
  });
  if (style === "blueprint" || !spot || spot.id !== sceneId) return null;
  return <ContactShadows key={sceneId} position={spot.pos} scale={spot.scale} far={spot.far} blur={2.4} opacity={0.38} resolution={512} color="#0b1020" />;
}
