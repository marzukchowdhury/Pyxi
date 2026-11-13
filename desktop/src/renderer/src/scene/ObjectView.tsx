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
import { ContactShadows, Edges, Environment, Lightformer, OrbitControls, Outlines, PerformanceMonitor, RoundedBox } from "@react-three/drei";
import { Canvas, type ThreeEvent, useFrame, useThree } from "@react-three/fiber";
import { type MutableRefObject, Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { Beat, ObjectScene, Part } from "../shared/protocol";
import { fromOrbit, orbitSpring, type OrbitSpring, smoothDamp, stepOrbit, toOrbit } from "./camera";
import { children, evaluate } from "./motion";
import { usePace } from "./pace";
import { useAsset } from "./assets";
import { buildKit } from "./kit";
import { buildGeometry, makeMaterial, prepareAssetMaterial } from "./parts";

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

interface Layer {
  mesh: THREE.Mesh;
  mat: THREE.MeshStandardMaterial;
}

interface Registered {
  part: Part;
  group: THREE.Group;
  /** The first layer (callouts and occlusion measure from it). */
  mesh: THREE.Mesh;
  mat: THREE.MeshStandardMaterial;
  /** Every mesh of the part with its own material: one for a built part; a real model's piece
   * keeps each of its original materials (painted metal, glass, decals...). */
  layers: Layer[];
  edges: THREE.Object3D | null;
  center: THREE.Vector3; // centre of the part, in the first layer's local space
}

const FOV = 32;
/** Remembered for the rest of the session once the GPU has shown it can't keep up. */
const quality: { tier: "high" | "low" } = { tier: "high" };
const SWING = 0.75; // seconds a camera move takes to mostly settle
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
  // Full quality (soft self-shadows, 2x pixels) unless the GPU can't keep up: then
  // it steps down once and stays down, rather than flickering between the two.
  const [tier, setTier] = useState<"high" | "low">(() => quality.tier);
  const lower = () => {
    quality.tier = "low";
    setTier("low");
  };
  const high = tier === "high";
  const bounds = useRef<Measured | null>(null);
  // Light, shadow and ground are fitted again whenever parts arrive (a model assembling itself).
  const fit = `${scene.id}:${scene.parts.length}`;

  return (
    <div className="object-view" style={{ width, height }}>
      <Canvas
        key={tier}
        frameloop="demand"
        gl={{ alpha: true, antialias: true, premultipliedAlpha: false, powerPreference: "high-performance" }}
        dpr={high ? [1, 2] : [1, 1.25]}
        shadows={high ? "percentage" : false}
        camera={{ fov: FOV, near: 0.02, far: 500, position: [0, 2, 14] }}
        onCreated={({ gl }) => {
          gl.setClearColor(0x000000, 0);
          gl.toneMapping = THREE.ACESFilmicToneMapping;
          gl.toneMappingExposure = 1.05;
        }}
        style={{ background: "transparent" }}
      >
        <Pace clock={clock} deps={[scene, beatIndex, cutaway, width, height]} />
        {high && <PerformanceMonitor ms={500} iterations={6} threshold={0.7} bounds={() => [40, 120]} onDecline={lower} onFallback={lower} />}
        <Measure registry={registry} sceneId={fit} out={bounds} />
        <Studio style={scene.style} bounds={bounds} sceneId={fit} shadows={high} />
        <Model scene={scene} clock={clock} beat={beat} cutaway={cutaway} registry={registry} onPick={onPick} />
        <Rig registry={registry} beat={beat} beatIndex={beatIndex} sceneId={scene.id} />
        <CalloutDriver registry={registry} callouts={callouts} beat={beat} width={width} height={height} />
        <Shadow bounds={bounds} sceneId={fit} style={scene.style} />
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

/** The model's resting size and place, measured once it's built (lights, shadows and AO scale to it). */
interface Measured {
  id: string;
  center: THREE.Vector3;
  radius: number;
  floor: number;
  footprint: number;
  height: number;
}

function Measure({ registry, sceneId, out }: { registry: MutableRefObject<Map<string, Registered>>; sceneId: string; out: MutableRefObject<Measured | null> }) {
  const box = useMemo(() => new THREE.Box3(), []);
  const [, force] = useState(0);
  useFrame(() => {
    if (out.current?.id === sceneId) return;
    boundsOf(registry.current, null, box);
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3());
    out.current = {
      id: sceneId,
      center: box.getCenter(new THREE.Vector3()),
      radius: Math.max(0.5, size.length() / 2),
      floor: box.min.y,
      footprint: Math.max(size.x, size.z),
      height: size.y,
    };
    force((n) => n + 1); // the light, shadow and AO re-fit to it
  });
  return null;
}

/**
 * A photographer's studio, generated locally (no HDR downloads): a big overhead softbox, two
 * tall strips that draw clean highlight lines down metal and paint, a warm kicker, an ice-blue
 * rim behind and a soft floor bounce. The key light casts soft shadows fitted to the model, so
 * parts shade each other.
 */
function Studio({ style, bounds, sceneId, shadows }: { style: ObjectScene["style"]; bounds: MutableRefObject<Measured | null>; sceneId: string; shadows: boolean }) {
  const key = useRef<THREE.DirectionalLight>(null);
  const m = bounds.current?.id === sceneId ? bounds.current : null;
  useEffect(() => {
    const l = key.current;
    if (!l || !m) return;
    const r = m.radius;
    l.position.copy(m.center).add(new THREE.Vector3(0.45, 1, 0.6).normalize().multiplyScalar(r * 3));
    l.target.position.copy(m.center);
    l.target.updateMatrixWorld();
    const cam = l.shadow.camera;
    cam.left = cam.bottom = -r * 1.15;
    cam.right = cam.top = r * 1.15;
    cam.near = r * 0.5;
    cam.far = r * 5.5;
    cam.updateProjectionMatrix();
    l.shadow.bias = -0.0004;
    l.shadow.normalBias = r * 0.012;
    l.shadow.needsUpdate = true;
  }, [m, shadows]);
  return (
    <>
      <ambientLight intensity={style === "blueprint" ? 1.4 : 0.12} />
      <hemisphereLight args={["#eef4ff", "#1a1d24", style === "blueprint" ? 0 : 0.35]} />
      <directionalLight ref={key} position={[4, 8, 6]} intensity={1.9} castShadow={shadows} shadow-mapSize={[1024, 1024]} shadow-radius={4} />
      <directionalLight position={[-6, 3, -4]} intensity={0.55} color="#cfe0ff" />
      <Environment resolution={256} frames={1} environmentIntensity={0.95}>
        <Lightformer form="rect" intensity={2.4} position={[0, 7, 1]} rotation-x={Math.PI / 2} scale={[12, 7, 1]} />
        <Lightformer form="rect" intensity={2.6} position={[-6.5, 1.5, 2]} rotation-y={Math.PI / 2.4} scale={[1.6, 9, 1]} />
        <Lightformer form="rect" intensity={1.6} position={[6.5, 1.5, 2]} rotation-y={-Math.PI / 2.4} scale={[1.2, 9, 1]} />
        <Lightformer form="rect" intensity={0.9} color="#ffe6c4" position={[4, 0.5, 6]} rotation-y={-Math.PI / 5} scale={[4, 3, 1]} />
        <Lightformer form="ring" intensity={2.2} color="#9ecbff" position={[0, 2, -7]} scale={4.5} />
        <Lightformer form="rect" intensity={0.35} color="#d8dde6" position={[0, -4, 0]} rotation-x={-Math.PI / 2} scale={[14, 14, 1]} />
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
    if (import.meta.env.DEV && registry.current.size) {
      // For scripts/first_frame_3d.py: when each model (and each new part count) first drew.
      const w = window as unknown as { __pyxi3d?: Record<string, number> };
      const k = `${scene.id}:${registry.current.size}`;
      (w.__pyxi3d ??= {})[k] ??= performance.now();
    }
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
      for (const { mesh, mat } of reg.layers) {
        const ud = mat.userData as {
          baseColor?: THREE.Color;
          baseMetal?: number;
          baseGlow?: THREE.Color;
          baseTransparent?: boolean;
          factor: number;
          baseEmissive: number;
        };
        const shown = (ghost ? 0.3 : 1) * op * f * ud.factor;
        // A see-through shell is pale silver, not its real paint: dark metal at low opacity on a
        // transparent stage reads as a smudge, and the outline of the thing gets lost.
        ud.baseColor ??= mat.color.clone();
        ud.baseMetal ??= mat.metalness;
        ud.baseGlow ??= mat.emissive.clone();
        mat.color.lerp(ghost ? GHOST : ud.baseColor, k);
        mat.metalness += ((ghost ? 0.05 : ud.baseMetal) - mat.metalness) * k;
        mat.opacity = shown;
        // A real model's glass stays glass: what was see-through is never made solid.
        const transparent = !!ud.baseTransparent || shown < 0.995;
        if (mat.transparent !== transparent) {
          mat.transparent = transparent;
          mat.needsUpdate = true;
        }
        mat.depthWrite = !transparent || shown > 0.6;
        // A see-through shell would still black out the parts it's meant to reveal.
        mesh.userData.casts ??= mesh.castShadow;
        mesh.castShadow = (mesh.userData.casts as boolean) && shown > 0.6;
        // A marker is only a place for a tag: never drawn, but it keeps its opacity for the tag.
        mesh.visible = shown > 0.004 && p.shape !== "marker";
        // Focus: a quiet ice-blue glow that breathes; pulses brighten emissive parts.
        if (p.material === "emissive" && p.shape !== "asset") mat.emissiveIntensity = ud.baseEmissive * (st.glow ? 0.55 + 0.9 * st.glow : 1);
        else if (isFocus && op > 0.05) {
          mat.emissive.copy(ICE);
          // A real model's paint is already lit and glossy: the full glow washes it out pale.
          mat.emissiveIntensity = (p.shape === "asset" ? 0.3 : 1) * (0.16 + 0.08 * Math.sin(t * 3.2) + st.glow * 0.4);
        } else if (st.glow) {
          mat.emissive.copy(ICE);
          mat.emissiveIntensity = st.glow * 0.5;
        } else {
          mat.emissive.copy(ud.baseGlow); // a real model's own lights and screens keep glowing
          mat.emissiveIntensity = ud.baseEmissive;
        }
      }
      // Ghosts (cutaway casing, or a part faded out of the way) keep their outline.
      if (reg.edges) reg.edges.visible = ((ghost || f < 0.5) && op > 0.05) || ((scene.style === "blueprint" || scene.style === "xray") && op > 0.05);
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
  // Keyed on what the part is, not the object: a model that arrives part by part is re-sent
  // whole each time, and the parts already on screen must keep their geometry.
  const sig = useMemo(() => JSON.stringify(part), [part]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const built = useMemo(() => buildGeometry(part), [sig]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const mat = useMemo(() => makeMaterial(part, style), [sig, style]);
  const asset = part.shape === "asset" && !!part.asset;
  const [assetLayers, setAssetLayers] = useState<Layer[] | null>(null);
  // A kit piece: several meshes, each with its own material (a tyre's rubber, its rim's metal).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const kit = useMemo(() => (part.shape === "kit" && part.kit ? buildKit(part.kit, part.size, part.axis, part.teeth) : null), [sig]);
  const kitMats = useMemo(
    () => kit?.map((l) => makeMaterial({ ...part, shape: "box", material: l.material, color: l.main ? (part.color ?? l.color ?? null) : (l.color ?? null) }, style)) ?? null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [kit, style],
  );
  const kitMeshes = useRef<(THREE.Mesh | null)[]>([]);
  useEffect(() => () => { kit?.forEach((l) => l.geometry.dispose()); kitMats?.forEach((m) => m.dispose()); }, [kit, kitMats]);
  useEffect(() => {
    if (!group.current) return;
    let layers: Layer[];
    let center: THREE.Vector3;
    if (kit && kitMats) {
      layers = kitMeshes.current.flatMap((m, i) => (m ? [{ mesh: m, mat: kitMats[i] }] : []));
      if (!layers.length) return;
      const box = new THREE.Box3();
      for (const l of layers) {
        l.mesh.geometry.computeBoundingBox();
        box.union(l.mesh.geometry.boundingBox!);
      }
      center = box.getCenter(new THREE.Vector3());
    } else if (asset) {
      if (!assetLayers?.length) return; // still loading
      layers = assetLayers;
      const box = new THREE.Box3();
      for (const l of layers) {
        l.mesh.geometry.computeBoundingBox();
        box.union(l.mesh.geometry.boundingBox!.clone().applyMatrix4(l.mesh.matrix));
      }
      // The first layer's local frame is the part's own (the piece sits at the part's centre).
      center = box.getCenter(new THREE.Vector3()).applyMatrix4(layers[0].mesh.matrix.clone().invert());
    } else {
      if (!mesh.current) return;
      built.geometry.computeBoundingBox();
      // A bent part's box is centred on thin air (the crook of a tail): its tag points at the part.
      center = built.anchor ? new THREE.Vector3(...built.anchor) : (built.geometry.boundingBox?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3());
      layers = [{ mesh: mesh.current, mat }];
    }
    registry.current.set(part.id, { part, group: group.current, mesh: layers[0].mesh, mat: layers[0].mat, layers, edges: asset || kit ? null : edges.current, center });
    return () => {
      registry.current.delete(part.id);
    };
  }, [part, built, mat, registry, asset, assetLayers, kit, kitMats]);
  useEffect(() => () => { built.geometry.dispose(); mat.dispose(); }, [built, mat]);
  // Flows and glowing parts are light, not matter: they never throw a shadow.
  const casts = part.shape !== "arrow" && part.material !== "emissive" && part.material !== "glass";

  // A click, not the end of a drag: turning the model round ended over a part every time, and
  // each time that asked Pyxi to "tell me more" about it (live: the same question three times
  // in twenty seconds, answered aloud each time). e.delta is how far the pointer moved.
  const click = onPick ? (e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); if (e.delta > 4) return; onPick(part); } : undefined;
  const hover = onPick ? (on: boolean) => (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); document.body.style.cursor = on ? "pointer" : ""; } : undefined;
  const lineColor = style === "blueprint" ? "#9ecbff" : "#dbe9ff";
  const rounded = part.shape === "rounded_box";
  const radius = Math.min(...part.size) * 0.2;
  return (
    <group ref={group}>
      {kit && kitMats ? (
        <group>
          {kit.map((l, i) => (
            <mesh key={i} ref={(m) => { kitMeshes.current[i] = m; }} geometry={l.geometry} material={kitMats[i]} castShadow={casts} receiveShadow
              onClick={click} onPointerOver={hover?.(true)} onPointerOut={hover?.(false)} />
          ))}
        </group>
      ) : asset ? (
        <Suspense fallback={null}>
          <AssetPiece part={part} style={style} onLayers={setAssetLayers} onClick={click} onHover={hover} />
        </Suspense>
      ) : rounded ? (
        <RoundedBox ref={mesh as never} args={part.size} radius={radius} smoothness={3} material={mat} castShadow={casts} receiveShadow onClick={click} onPointerOver={hover?.(true)} onPointerOut={hover?.(false)}>
          <Edges ref={edges as never} threshold={25} color={lineColor} visible={false} />
          {style === "sticker" && <Outlines thickness={2.5} screenspace color="#ffffff" />}
        </RoundedBox>
      ) : (
        <mesh ref={mesh} geometry={built.geometry} material={mat} scale={built.scale} castShadow={casts} receiveShadow onClick={click} onPointerOver={hover?.(true)} onPointerOut={hover?.(false)}>
          <Edges ref={edges as never} threshold={25} color={lineColor} visible={false} />
          {style === "sticker" && <Outlines thickness={2.5} screenspace color="#ffffff" />}
        </mesh>
      )}
      {kids}
    </group>
  );
}

/** A piece of a real model from the library: the file's own meshes and materials (cloned, so
 * this part can fade and glow on its own), centred on the part. */
function AssetPiece({ part, style, onLayers, onClick, onHover }: {
  part: Part;
  style: ObjectScene["style"];
  onLayers: (layers: Layer[] | null) => void;
  onClick?: (e: ThreeEvent<MouseEvent>) => void;
  onHover?: (on: boolean) => (e: ThreeEvent<PointerEvent>) => void;
}) {
  const gltf = useAsset(part.asset!);
  const piece = useMemo(() => {
    const src = gltf.scene.getObjectByName(`part_${part.node ?? part.id}`);
    if (!src) return null;
    const copy = src.clone(true);
    copy.position.set(0, 0, 0);
    copy.quaternion.identity();
    copy.scale.set(1, 1, 1);
    copy.updateMatrixWorld(true);
    const layers: Layer[] = [];
    copy.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh) return;
      m.material = prepareAssetMaterial(m.material as THREE.MeshStandardMaterial, style);
      m.castShadow = true;
      m.receiveShadow = true;
      layers.push({ mesh: m, mat: m.material as THREE.MeshStandardMaterial });
    });
    return { copy, layers };
  }, [gltf, part.node, part.id, part.asset, style]);
  useEffect(() => {
    onLayers(piece?.layers ?? null);
    return () => piece?.layers.forEach((l) => l.mat.dispose()); // the geometry is the file's, shared
  }, [piece, onLayers]);
  if (!piece) return null;
  return <primitive object={piece.copy} onClick={onClick} onPointerOver={onHover?.(true)} onPointerOut={onHover?.(false)} />;
}

/** World-space bounds of the visible parts (or just the focus), recomputed every frame. */
function boundsOf(registry: Map<string, Registered>, ids: Set<string> | null, out: THREE.Box3): THREE.Box3 {
  out.makeEmpty();
  const box = new THREE.Box3();
  for (const [id, r] of registry) {
    if (ids && !ids.has(id)) continue;
    if (!r.mesh.visible || r.mat.opacity < 0.05) continue;
    for (const { mesh } of r.layers) {
      const g = mesh.geometry;
      if (!g.boundingBox) g.computeBoundingBox();
      box.copy(g.boundingBox!).applyMatrix4(mesh.matrixWorld);
      out.union(box);
    }
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
    for (const { mesh } of r.layers) {
      const g = mesh.geometry;
      let local = SAMPLES.get(g);
      if (!local) {
        // A mesh between two models (its geometry just let go) or an empty shape has no points.
        const pos = g?.getAttribute("position");
        if (!pos) continue;
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
        v.set(local[i], local[i + 1], local[i + 2]).applyMatrix4(mesh.matrixWorld);
        n++;
      }
    }
  }
  return pool.slice(0, n);
}

/** Frames on demand: every frame while the model plays, a few seconds after anything changes
 * (fades, ghosting, a part arriving), and nothing once it's paused or done. */
function Pace({ clock, deps }: { clock: MutableRefObject<Clock>; deps: unknown[] }) {
  const wake = usePace(() => clock.current.playing, deps);
  // The panel flips `playing` on the clock without re-rendering us: notice it.
  useEffect(() => {
    let was = clock.current.playing;
    const id = window.setInterval(() => {
      const now = clock.current.playing;
      if (now !== was) wake();
      was = now;
    }, 100);
    return () => window.clearInterval(id);
  }, [clock, wake]);
  return null;
}

/** Frames the beat's subject from the beat's angle and follows it; hands over when you drag. */
function Rig({ registry, beat, beatIndex, sceneId }: { registry: MutableRefObject<Map<string, Registered>>; beat: Beat; beatIndex: number; sceneId: string }) {
  const controls = useRef<OrbitControlsImpl>(null);
  const { camera, size, invalidate } = useThree();
  const manual = useRef(false);
  const first = useRef(true);
  const spring = useRef<OrbitSpring | null>(null);
  const targetVel = useRef([{ v: 0 }, { v: 0 }, { v: 0 }]);
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
      // Arrive from further out and a little round to the side, so the model turns into view
      // as it settles, like a product reveal.
      const o = toOrbit(dir.x, dir.y, dir.z);
      o.radius = dist * 1.8;
      o.theta -= 0.55;
      o.phi = Math.max(0.2, o.phi - 0.12);
      camera.position.copy(sphere.center).add(tmp.set(...fromOrbit(o)));
      c.target.copy(sphere.center);
      first.current = false;
      spring.current = null;
    }
    if (manual.current) spring.current = null; // pick up from wherever they leave it
    else {
      const step = Math.min(dt, 0.05);
      // The look-at point and the orbit both follow critically damped springs: the camera
      // swings round the subject between beats instead of cutting a straight line through it.
      const s = (spring.current ??= orbitSpring(toOrbit(...camera.position.clone().sub(c.target).toArray())));
      c.target.set(
        smoothDamp(c.target.x, goal.target.x, targetVel.current[0], SWING, step),
        smoothDamp(c.target.y, goal.target.y, targetVel.current[1], SWING, step),
        smoothDamp(c.target.z, goal.target.z, targetVel.current[2], SWING, step),
      );
      const g = goal.pos.clone().sub(goal.target);
      const o = stepOrbit(s, toOrbit(g.x, g.y, g.z), SWING, step);
      camera.position.copy(c.target).add(tmp.set(...fromOrbit(o)));
      // Still on its way: keep drawing until it arrives (on-demand frames would freeze it).
      const near = Math.max(1e-3, goal.pos.distanceTo(goal.target) * 1e-3);
      if (camera.position.distanceTo(goal.pos) > near || c.target.distanceTo(goal.target) > near) invalidate();
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
      const op = r.mesh.visible || r.part.shape === "marker" ? Math.min(1, r.mat.opacity / Math.max(0.05, r.mat.userData.factor as number)) : 0;
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

/** A soft contact shadow under where the model starts, so it sits on something: a wide faint
 * penumbra plus a tight dark core where it touches. Sized to the footprint, measured once. */
function Shadow({ bounds, sceneId, style }: { bounds: MutableRefObject<Measured | null>; sceneId: string; style: ObjectScene["style"] }) {
  const m = bounds.current?.id === sceneId ? bounds.current : null;
  if (style === "blueprint" || !m) return null;
  const pos: [number, number, number] = [m.center.x, m.floor - 0.01, m.center.z];
  return (
    <>
      <ContactShadows key={`${sceneId}-wide`} position={pos} scale={m.footprint * 1.6} far={Math.max(0.3, m.height * 0.5)} blur={3.2} opacity={0.3} resolution={256} color="#0b1020" />
      <ContactShadows key={`${sceneId}-core`} position={[pos[0], pos[1] + 0.005, pos[2]]} scale={m.footprint * 1.25} far={Math.max(0.12, m.height * 0.1)} blur={1.1} opacity={0.42} resolution={256} color="#05070c" />
    </>
  );
}
