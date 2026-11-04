/**
 * The 3D explanation renderer: a SceneSpec becomes an animated, explorable React Three Fiber
 * scene. Nodes fly in to their layout positions (exploded pieces lift straight off the real
 * screen), flow edges carry travelling particles, and each narrated step moves the camera and
 * spotlights the nodes it is about.
 */
import { ContactShadows, Edges, Html, OrbitControls, Outlines, QuadraticBezierLine, RoundedBox } from "@react-three/drei";
import { Canvas, type ThreeEvent, useFrame, useLoader, useThree } from "@react-three/fiber";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { SceneNode, SceneSpec } from "../shared/protocol";
import { computeLayout, fitDistance, focusBounds, type Layout, type Placed, type Vec3 } from "./layout";
import { nodeColor, type Theme, THEMES } from "./themes";

export interface Viewport {
  width: number;
  height: number;
  scale: number;
  origin: { x: number; y: number };
}

interface Props {
  scene: SceneSpec;
  step: number;
  hologram: boolean;
  viewport: Viewport;
  cropsBase: string;
  onHover?: (node: SceneNode | null) => void;
}

const FOV = 34; // a longer lens: calmer perspective, objects read larger
const ease = (t: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);

export function SceneView({ scene, step, hologram, viewport, cropsBase, onHover }: Props) {
  const layout = useMemo(() => computeLayout(scene, viewport), [scene, viewport]);
  const theme = THEMES[scene.theme] ?? THEMES.aurora;
  const current = scene.steps[Math.min(step, scene.steps.length - 1)];
  const revealed = useMemo(() => {
    // Progressive disclosure: a node with a "reveal" step stays hidden until that step.
    const firstReveal = new Map<string, number>();
    scene.steps.forEach((s, i) => s.reveal?.forEach((id) => !firstReveal.has(id) && firstReveal.set(id, i)));
    return (id: string) => (firstReveal.get(id) ?? -1) <= step;
  }, [scene, step]);
  const exploded = scene.layout === "exploded";
  const lifted = !exploded || step > 0 || !hologram; // hologram exploded views start flat on the screen

  return (
    <Canvas
      gl={{ alpha: true, antialias: true, premultipliedAlpha: false }}
      dpr={[1, 2]}
      camera={{ fov: FOV, near: 0.05, far: 400, position: [0, 0, 14] }}
      onCreated={({ gl }) => gl.setClearColor(0x000000, 0)}
      style={{ background: "transparent" }}
    >
      <ambientLight intensity={0.45} />
      <hemisphereLight args={["#dbe8ff", "#0a0c12", 0.32]} />
      <directionalLight position={[5, 9, 7]} intensity={1.35} />
      <pointLight position={[0, 2, -9]} intensity={90} color={theme.rim} distance={40} />
      <pointLight position={[-9, -3, 5]} intensity={22} color="#c6b8ff" distance={30} />
      {!exploded && (
        <ContactShadows position={[0, -layout.extent[1] / 2 - 0.4, 0]} scale={Math.max(layout.extent[0], layout.extent[1]) * 1.6} blur={3} opacity={0.42} far={layout.radius * 2} color="#000000" />
      )}
      {theme.grid && hologram && !exploded && (
        <gridHelper args={[60, 40, theme.grid, theme.grid]} position={[0, -layout.extent[1] / 2 - 0.45, 0]}>
          <lineBasicMaterial attach="material" color={theme.grid} transparent opacity={0.35} />
        </gridHelper>
      )}
      <Suspense fallback={null}>
        {scene.edges.map((e, i) => {
          const a = layout.nodes.get(e.from);
          const b = layout.nodes.get(e.to);
          if (!a || !b || !revealed(e.from) || !revealed(e.to)) return null;
          const key = `${e.from}->${e.to}`;
          const active = !current?.edges?.length || current.edges.includes(key) || current.edges.includes(`${e.to}->${e.from}`);
          return <Edge3D key={i} from={a} to={b} edge={e} theme={theme} active={active} />;
        })}
        {scene.nodes.map((n, i) =>
          revealed(n.id) ? (
            <Node3D
              key={n.id}
              node={n}
              index={i}
              placed={layout.nodes.get(n.id)!}
              theme={theme}
              lifted={lifted}
              dimmed={!!current?.focus.length && !current.focus.includes(n.id)}
              focused={!!current?.focus.includes(n.id)}
              cropsBase={cropsBase}
              onHover={onHover}
            />
          ) : null,
        )}
      </Suspense>
      <CameraRig layout={layout} scene={scene} step={step} hologram={hologram} viewport={viewport} />
      <OrbitControls makeDefault enableDamping dampingFactor={0.08} rotateSpeed={0.7} zoomSpeed={0.8} />
    </Canvas>
  );
}

// ---------------------------------------------------------------------------- camera
function CameraRig({ layout, scene, step, hologram, viewport }: { layout: Layout; scene: SceneSpec; step: number; hologram: boolean; viewport: Viewport }) {
  const { camera, controls } = useThree() as unknown as { camera: THREE.PerspectiveCamera; controls: OrbitControlsImpl | null };
  const anim = useRef<{ from: THREE.Vector3; to: THREE.Vector3; tFrom: THREE.Vector3; tTo: THREE.Vector3; t: number } | null>(null);

  const screenDistance = viewport.height / 100 / 2 / Math.tan(((FOV / 2) * Math.PI) / 180);

  useEffect(() => {
    const s = scene.steps[Math.min(step, scene.steps.length - 1)];
    const exploded = scene.layout === "exploded";
    const focusing = !!(s && s.focus.length && s.camera !== "overview");
    const box = focusing ? focusBounds(layout, s!.focus) : layout;
    let target: Vec3 = box.center;
    // Focus shots keep some context: never tighter than ~45% of the whole scene.
    const extent: Vec3 = focusing
      ? [Math.max(box.extent[0] * 1.4, layout.extent[0] * 0.45), Math.max(box.extent[1] * 1.4, layout.extent[1] * 0.45), box.extent[2]]
      : layout.extent;
    const dist = Math.max(4.5, fitDistance(extent, FOV, Math.max(0.5, camera.aspect || 1.6)));
    const dir = new THREE.Vector3(0.18, 0.2, 1);
    const preset = s?.camera ?? "overview";
    if (preset === "top") dir.set(0.05, 1, 0.35);
    else if (preset === "side") dir.set(1, 0.25, 0.55);
    else if (preset === "front") dir.set(0, 0.04, 1);
    if (exploded && hologram && step === 0) {
      // Frame the real screen exactly: z = 0 maps 1:1 onto the window's pixels.
      target = [0, 0, 0];
      dir.set(0, 0, 1);
    } else if (exploded) dir.set(0.75, 0.32, 1);
    const distance = exploded && hologram && step === 0 ? screenDistance : exploded ? Math.max(dist, layout.radius * 1.3) : dist;
    const to = new THREE.Vector3(...target).add(dir.normalize().multiplyScalar(distance));
    const tTo = new THREE.Vector3(...target);
    const tFrom = controls ? controls.target.clone() : new THREE.Vector3();
    if (!anim.current && exploded && hologram) {
      camera.position.set(0, 0, screenDistance);
      tFrom.set(0, 0, 0);
    }
    anim.current = { from: camera.position.clone(), to, tFrom, tTo, t: 0 };
  }, [scene, step, layout, hologram, camera, controls, screenDistance]);

  useFrame((_, dt) => {
    const a = anim.current;
    if (!a || a.t >= 1) return;
    a.t = Math.min(1, a.t + dt / 1.4);
    const k = ease(a.t);
    camera.position.lerpVectors(a.from, a.to, k);
    const target = new THREE.Vector3().lerpVectors(a.tFrom, a.tTo, k);
    if (controls) {
      controls.target.copy(target);
      controls.update();
    } else camera.lookAt(target);
  });
  return null;
}

// ---------------------------------------------------------------------------- nodes
function Node3D(props: {
  node: SceneNode;
  index: number;
  placed: Placed;
  theme: Theme;
  lifted: boolean;
  dimmed: boolean;
  focused: boolean;
  cropsBase: string;
  onHover?: (node: SceneNode | null) => void;
}) {
  const { node, index, placed, theme, lifted, dimmed, focused, cropsBase, onHover } = props;
  const group = useRef<THREE.Group>(null);
  const born = useRef(performance.now() + index * 45);
  const [hover, setHover] = useState(false);
  const color = nodeColor(theme, index, node.color);
  const start = placed.start ?? [0, 0, 0];
  const target = lifted ? placed.pos : start;

  useFrame(() => {
    const g = group.current;
    if (!g) return;
    const t = ease((performance.now() - born.current) / 900);
    const goal = new THREE.Vector3(...target);
    if (t < 1 && !placed.start) {
      g.position.lerpVectors(new THREE.Vector3(0, 0, 0), goal, t);
      g.scale.setScalar(Math.max(0.001, t));
    } else {
      g.position.lerp(goal, 0.08);
      const s = (focused ? 1.08 : 1) * (hover ? 1.05 : 1);
      g.scale.lerp(new THREE.Vector3(s, s, s), 0.15);
    }
    if (!placed.start) g.position.y += Math.sin(performance.now() / 900 + index) * 0.0025;
    g.traverse((o) => {
      const m = (o as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined;
      if (m && "opacity" in m) {
        const base = (m.userData.baseOpacity as number | undefined) ?? 1;
        m.opacity += ((dimmed ? base * 0.4 : base) - m.opacity) * 0.12;
      }
    });
  });

  const enter = (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    setHover(true);
    onHover?.(node);
  };
  const leave = () => {
    setHover(false);
    onHover?.(null);
  };

  const [w, h] = placed.size;
  const isFlat = node.shape === "screen" || node.shape === "card";
  return (
    <group ref={group} position={placed.start ?? [0, 0, 0]} onPointerOver={enter} onPointerOut={leave}>
      <Shape node={node} color={color} theme={theme} w={w} h={h} cropsBase={cropsBase} glow={node.glow || focused} />
      {(node.glow || focused) && !isFlat && (
        <mesh scale={1.55 * (node.size ?? 1)}>
          <sphereGeometry args={[0.6, 24, 24]} />
          <meshBasicMaterial color={color} transparent opacity={0.09} blending={THREE.AdditiveBlending} depthWrite={false} />
        </mesh>
      )}
      {placed.start && !lifted ? null : (
        <Html position={[0, isFlat ? h / 2 + 0.1 : -(0.62 * (node.size ?? 1) + 0.34), 0]} center zIndexRange={[20, 0]}
          style={{ pointerEvents: "none" }}>
          <div className={`node-label ${placed.start ? "small" : ""} ${dimmed ? "dim" : ""} ${focused ? "focus" : ""}`} style={{ ["--accent" as string]: color }}>
            <b>{node.label}</b>
            {node.detail && !placed.start && <span>{node.detail}</span>}
          </div>
        </Html>
      )}
    </group>
  );
}

/** Dark clear-coated glass with a luminous rim: colour lives in light, not paint. */
function material(color: string, glow: boolean, opacity = 1) {
  return (
    <>
      <meshPhysicalMaterial color="#141823" emissive={color} emissiveIntensity={glow ? 0.42 : 0.06} roughness={0.22}
        metalness={0.45} clearcoat={1} clearcoatRoughness={0.08} transparent opacity={opacity} userData={{ baseOpacity: opacity }} />
      <Outlines thickness={glow ? 0.035 : 0.022} color={color} transparent opacity={glow ? 1 : 0.75} />
    </>
  );
}

function Shape({ node, color, theme, w, h, cropsBase, glow }: { node: SceneNode; color: string; theme: Theme; w: number; h: number; cropsBase: string; glow: boolean }) {
  const s = node.size ?? 1;
  switch (node.shape) {
    case "sphere":
      return <mesh><sphereGeometry args={[0.6 * s, 40, 40]} />{material(color, glow)}</mesh>;
    case "cylinder":
      return <mesh><cylinderGeometry args={[0.55 * s, 0.55 * s, 1.1 * s, 40]} />{material(color, glow)}</mesh>;
    case "cone":
      return <mesh><coneGeometry args={[0.6 * s, 1.2 * s, 40]} />{material(color, glow)}</mesh>;
    case "torus":
      return <mesh rotation={[Math.PI / 2.6, 0, 0]}><torusGeometry args={[0.55 * s, 0.2 * s, 24, 64]} />{material(color, glow)}</mesh>;
    case "capsule":
      return <mesh><capsuleGeometry args={[0.38 * s, 0.6 * s, 8, 24]} />{material(color, glow)}</mesh>;
    case "chip":
      return <mesh rotation={[0, 0, Math.PI / 2]}><capsuleGeometry args={[0.26 * s, 1.1 * s, 8, 24]} />{material(color, glow)}</mesh>;
    case "card":
      return (
        <RoundedBox args={[w, h, 0.12]} radius={0.08} smoothness={4}>
          <meshPhysicalMaterial color={theme.panel} emissive={color} emissiveIntensity={glow ? 0.22 : 0.04} roughness={0.3} clearcoat={1}
            transparent opacity={0.9} userData={{ baseOpacity: 0.9 }} />
          <Edges color={color} threshold={20} />
        </RoundedBox>
      );
    case "screen":
      return <ScreenPiece node={node} color={color} w={w} h={h} cropsBase={cropsBase} glow={glow} />;
    case "server":
      return (
        <group>
          {[-0.42, 0, 0.42].map((y) => (
            <RoundedBox key={y} args={[1.1 * s, 0.34 * s, 0.8 * s]} radius={0.06} position={[0, y * s, 0]}>{material(color, glow)}</RoundedBox>
          ))}
          {[-0.42, 0, 0.42].map((y) => (
            <mesh key={`l${y}`} position={[0.38 * s, y * s, 0.41 * s]}><sphereGeometry args={[0.045 * s, 12, 12]} /><meshBasicMaterial color="#86efac" /></mesh>
          ))}
        </group>
      );
    case "user":
      return (
        <group>
          <mesh position={[0, 0.42 * s, 0]}><sphereGeometry args={[0.3 * s, 32, 32]} />{material(color, glow)}</mesh>
          <mesh position={[0, -0.28 * s, 0]}><capsuleGeometry args={[0.36 * s, 0.35 * s, 8, 24]} />{material(color, glow)}</mesh>
        </group>
      );
    case "database":
      return (
        <group>
          <mesh><cylinderGeometry args={[0.55 * s, 0.55 * s, 1.1 * s, 40]} />{material(color, glow)}</mesh>
          {[-0.2, 0.2].map((y) => (
            <mesh key={y} position={[0, y * s, 0]} rotation={[Math.PI / 2, 0, 0]}><torusGeometry args={[0.56 * s, 0.025 * s, 8, 48]} /><meshBasicMaterial color="#ffffff" transparent opacity={0.5} /></mesh>
          ))}
        </group>
      );
    case "cloud":
      return (
        <group>
          {[[0, 0, 0, 0.5], [0.45, -0.1, 0, 0.38], [-0.45, -0.1, 0, 0.38], [0.2, 0.28, 0, 0.36], [-0.22, 0.25, 0.05, 0.32]].map(([x, y, z, r], i) => (
            <mesh key={i} position={[x * s, y * s, z * s]}><sphereGeometry args={[r * s, 28, 28]} />{material(color, glow)}</mesh>
          ))}
        </group>
      );
    default:
      return <RoundedBox args={[1.1 * s, 0.9 * s, 0.9 * s]} radius={0.14} smoothness={4}>{material(color, glow)}</RoundedBox>;
  }
}

function ScreenPiece({ node, color, w, h, cropsBase, glow }: { node: SceneNode; color: string; w: number; h: number; cropsBase: string; glow: boolean }) {
  if (node.image) {
    return (
      <Suspense fallback={<GlassPanel color={color} w={w} h={h} glow={glow} />}>
        <TexturedPanel url={`${cropsBase}/crops/${node.image}`} color={color} w={w} h={h} glow={glow} />
      </Suspense>
    );
  }
  return <GlassPanel color={color} w={w} h={h} glow={glow} />;
}

function TexturedPanel({ url, color, w, h, glow }: { url: string; color: string; w: number; h: number; glow: boolean }) {
  const texture = useLoader(THREE.TextureLoader, url);
  texture.colorSpace = THREE.SRGBColorSpace;
  return (
    <group>
      <mesh>
        <planeGeometry args={[w, h]} />
        <meshBasicMaterial map={texture} transparent opacity={1} side={THREE.DoubleSide} userData={{ baseOpacity: 1 }} toneMapped={false} />
      </mesh>
      <lineSegments>
        <edgesGeometry args={[new THREE.PlaneGeometry(w, h)]} />
        <lineBasicMaterial color={color} transparent opacity={glow ? 1 : 0.7} />
      </lineSegments>
    </group>
  );
}

function GlassPanel({ color, w, h, glow }: { color: string; w: number; h: number; glow: boolean }) {
  return (
    <group>
      <mesh>
        <planeGeometry args={[w, h]} />
        <meshBasicMaterial color={color} transparent opacity={glow ? 0.32 : 0.16} side={THREE.DoubleSide} userData={{ baseOpacity: glow ? 0.32 : 0.16 }} depthWrite={false} />
      </mesh>
      <lineSegments>
        <edgesGeometry args={[new THREE.PlaneGeometry(w, h)]} />
        <lineBasicMaterial color={color} transparent opacity={0.85} />
      </lineSegments>
    </group>
  );
}

// ---------------------------------------------------------------------------- edges
function Edge3D({ from, to, edge, theme, active }: { from: Placed; to: Placed; edge: SceneSpec["edges"][number]; theme: Theme; active: boolean }) {
  const color = edge.color ?? theme.edge;
  const { start, end, mid, curve } = useMemo(() => {
    const a = new THREE.Vector3(...from.pos);
    const b = new THREE.Vector3(...to.pos);
    const m = a.clone().lerp(b, 0.5);
    const lift = Math.max(0.6, a.distanceTo(b) * 0.18);
    m.y += lift;
    m.z += lift * 0.5;
    return { start: a, end: b, mid: m, curve: new THREE.QuadraticBezierCurve3(a, m, b) };
  }, [from, to]);
  const born = useRef(performance.now());
  const line = useRef<THREE.Object3D>(null);
  const opacity = active ? 0.95 : 0.18;

  useFrame(() => {
    const t = ease((performance.now() - born.current - 500) / 700);
    const mat = (line.current as unknown as { material?: THREE.Material & { opacity: number } })?.material;
    if (mat) mat.opacity = t * opacity;
  });

  const arrowAt = curve.getPoint(0.9);
  const tangent = curve.getTangent(0.9).normalize();
  const arrowQuat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), tangent);
  const label = curve.getPoint(0.5).add(new THREE.Vector3(0, 0.3, 0));
  return (
    <group>
      {/* a soft dark underlay keeps light edges readable over light interfaces */}
      <QuadraticBezierLine start={start} end={end} mid={mid} color="#05070c" lineWidth={active ? 4 : 2.6} transparent opacity={active ? 0.22 : 0.1} />
      <QuadraticBezierLine ref={line as never} start={start} end={end} mid={mid} color={color} lineWidth={active ? 1.6 : 0.9}
        dashed={edge.kind === "dashed"} dashSize={0.25} gapSize={0.18} transparent opacity={0} />
      {edge.kind !== "link" && (
        <mesh position={arrowAt} quaternion={arrowQuat}>
          <coneGeometry args={[0.12, 0.32, 16]} />
          <meshBasicMaterial color={color} transparent opacity={opacity} />
        </mesh>
      )}
      {edge.kind === "flow" && active && <Particles curve={curve} color={color} reverse={false} />}
      {edge.kind === "flow" && active && edge.bidirectional && <Particles curve={curve} color={color} reverse />}
      {edge.label && (
        <Html position={label} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
          <div className={`edge-label ${active ? "" : "dim"}`}>{edge.label}</div>
        </Html>
      )}
    </group>
  );
}

function Particles({ curve, color, reverse }: { curve: THREE.QuadraticBezierCurve3; color: string; reverse: boolean }) {
  const refs = useRef<THREE.Mesh[]>([]);
  const n = 5;
  useFrame(() => {
    const now = performance.now() / 1600;
    refs.current.forEach((m, i) => {
      if (!m) return;
      let t = (now + i / n) % 1;
      if (reverse) t = 1 - t;
      m.position.copy(curve.getPoint(t));
      const s = 0.6 + 0.4 * Math.sin(t * Math.PI);
      m.scale.setScalar(s);
    });
  });
  return (
    <>
      {Array.from({ length: n }, (_, i) => (
        <mesh key={i} ref={(m) => { if (m) refs.current[i] = m; }}>
          <sphereGeometry args={[0.05, 12, 12]} />
          <meshBasicMaterial color={color} transparent opacity={0.95} blending={THREE.AdditiveBlending} depthWrite={false} />
        </mesh>
      ))}
    </>
  );
}
