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
const ICE = new THREE.Color("#9ecbff");
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
            <circle ref={(el) => { slot(callouts.current, p.id).dot = el; }} r="2.6" className="callout-dot" />
          </g>
        ))}
      </svg>
      {labelled.map((p) => (
        <div key={p.id} ref={(el) => { slot(callouts.current, p.id).el = el; }} className="callout" style={{ opacity: 0 }}>
          <b>{p.label}</b>
          {p.note && <span>{p.note}</span>}
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
      const parentOp = p.parent ? opacity.current.get(p.parent) ?? 1 : 1;
      const op = Math.max(0, Math.min(1, st.opacity)) * parentOp;
      opacity.current.set(p.id, op);
      const isFocus = focus.has(p.id) || (p.parent ? focused.current.get(p.parent) ?? false : false);
      focused.current.set(p.id, isFocus);
      // Cutaway: the casing becomes a ghost with its outline still drawn.
      const ghost = cutaway && p.casing;
      const f = (fade.current.get(p.id) ?? 1) + ((blocking.has(p.id) ? 0.08 : 1) - (fade.current.get(p.id) ?? 1)) * k;
      fade.current.set(p.id, f);
      const shown = (ghost ? 0.16 : 1) * op * f * (reg.mat.userData.factor as number);
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
    const margin = beat.camera === "close" ? 1.06 : beat.camera === "wide" ? 1.3 : 1.16;
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
    let dist = 0.5;
    for (let i = 0; i < 8; i++) {
      const corner = new THREE.Vector3(i & 1 ? framed.max.x : framed.min.x, i & 2 ? framed.max.y : framed.min.y, i & 4 ? framed.max.z : framed.min.z).sub(sphere.center);
      const depth = corner.dot(dir); // toward the camera
      dist = Math.max(dist, Math.abs(corner.dot(right)) / th + depth, Math.abs(corner.dot(up)) / tv + depth);
    }
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

/** Projects labelled parts to the screen and lays their callouts out in two clean columns. */
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
  useFrame(() => {
    const items: { id: string; ax: number; ay: number; op: number; left: boolean }[] = [];
    for (const [id, slotted] of callouts.current) {
      const r = registry.current.get(id);
      if (!r || !slotted.el) continue;
      v.copy(r.center).applyMatrix4(r.mesh.matrixWorld).project(camera);
      const behind = v.z > 1;
      const ax = (v.x * 0.5 + 0.5) * width;
      const ay = (-v.y * 0.5 + 0.5) * height;
      const op = r.mesh.visible ? Math.min(1, r.mat.opacity / Math.max(0.05, r.mat.userData.factor as number)) : 0;
      if (behind || op < 0.3 || ax < 0 || ax > width || ay < 0 || ay > height) {
        hide(slotted);
        continue;
      }
      items.push({ id, ax, ay, op, left: ax < width / 2 });
    }
    for (const side of [true, false]) {
      const col = items.filter((i) => i.left === side).sort((a, b) => a.ay - b.ay);
      if (!col.length) continue;
      // Columns sit beside the model but never let a label run off the stage.
      const edgeX = side
        ? Math.max(170, Math.min(...col.map((i) => i.ax)) - 70)
        : Math.min(width - 170, Math.max(...col.map((i) => i.ax)) + 70);
      // Spread labels so they never overlap, then pull the stack back inside the stage.
      const ys = col.map((i) => i.ay);
      for (let i = 1; i < ys.length; i++) ys[i] = Math.max(ys[i], ys[i - 1] + 34);
      const overflow = ys[ys.length - 1] - (height - 20);
      if (overflow > 0) for (let i = 0; i < ys.length; i++) ys[i] -= overflow;
      for (let i = ys.length - 2; i >= 0; i--) ys[i] = Math.min(ys[i], ys[i + 1] - 34);
      col.forEach((item, i) => {
        const s = callouts.current.get(item.id)!;
        const ly = Math.max(16, ys[i]);
        const dim = focus.size && !focus.has(item.id) ? 0.4 : 1;
        const elbow = side ? edgeX + 18 : edgeX - 18;
        s.el!.style.opacity = String(item.op * dim);
        s.el!.style.transform = `translate(${edgeX}px, ${ly}px) translate(${side ? "-100%" : "0"}, -50%)`;
        s.el!.classList.toggle("on", focus.has(item.id));
        s.line?.setAttribute("d", `M ${item.ax} ${item.ay} L ${elbow} ${ly} L ${edgeX + (side ? 4 : -4)} ${ly}`);
        s.line?.style.setProperty("opacity", String(item.op * dim * 0.9));
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
