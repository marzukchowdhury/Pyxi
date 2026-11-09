/**
 * The swarm: one WebGL field of fireflies behind the whole home page. Thousands of gold and ice
 * points drift in real depth, part around your cursor, and gather into shapes as you scroll: a
 * galaxy turning round the hero's glass, a chat box, a screen, the controls Pyxi reads, a chip
 * taken apart in 3D, an Allow card, and at the end Pyxi itself.
 *
 * Every shape is either drawn with the 2D canvas API and sampled into points (blue pixels become
 * ice, red become gold), or generated straight in 3D. Each point is on its own spring toward its
 * place, so a change of shape is a flight, never a cut. Raw WebGL, no library.
 *
 * Which shape is up comes from the page: the element with data-swarm that crosses the middle of
 * the viewport names it (the story section rewrites its own as its steps go by).
 * Reduced motion: no flight and no drift, each shape is simply there.
 */
const CAM = 4; // camera distance; at z = 0 the view is y in [-1, 1] and x in [-aspect, aspect]
const ICE = "#00f";
const GOLD = "#f00";
const FONT = '"Inter Variable", Inter, "Segoe UI", sans-serif';
const rand = Math.random;
const gauss = () => (rand() + rand() + rand() + rand() - 2) / 2;

// ------------------------------------------------------------------ shapes, drawn then sampled
function rr(x, a, b, w, h, r) {
  x.beginPath();
  if (x.roundRect) x.roundRect(a, b, w, h, r);
  else x.rect(a, b, w, h);
}
function line(x, a, b, c, d) {
  x.beginPath();
  x.moveTo(a, b);
  x.lineTo(c, d);
  x.stroke();
}
function arrow(x, tx, ty, s) {
  // a mouse pointer with its tip at (tx, ty)
  x.beginPath();
  x.moveTo(tx, ty);
  x.lineTo(tx, ty + 17 * s);
  x.lineTo(tx + 4.6 * s, ty + 13 * s);
  x.lineTo(tx + 7.6 * s, ty + 19.6 * s);
  x.lineTo(tx + 10.4 * s, ty + 18.4 * s);
  x.lineTo(tx + 7.4 * s, ty + 11.8 * s);
  x.lineTo(tx + 13 * s, ty + 11.8 * s);
  x.closePath();
}
const DRAW = {
  // a chat window: where most AI lives
  bubble(x) {
    x.strokeStyle = x.fillStyle = ICE;
    x.lineWidth = 4;
    rr(x, 170, 28, 300, 344, 24);
    x.stroke();
    line(x, 170, 80, 470, 80);
    [196, 216, 236].forEach((cx) => {
      x.beginPath();
      x.arc(cx, 54, 5, 0, 7);
      x.fill();
    });
    x.lineWidth = 3.5;
    rr(x, 190, 100, 176, 42, 16);
    x.stroke();
    rr(x, 262, 156, 188, 60, 16);
    x.stroke();
    rr(x, 190, 230, 206, 42, 16);
    x.stroke();
    x.lineWidth = 3;
    line(x, 208, 121, 340, 121);
    line(x, 282, 176, 430, 176);
    line(x, 282, 196, 392, 196);
    line(x, 208, 251, 372, 251);
    x.lineWidth = 3.5;
    rr(x, 190, 306, 260, 46, 23);
    x.stroke();
    x.beginPath();
    x.arc(426, 329, 14, 0, 7);
    x.fill();
  },
  // a monitor with a window on it, your cursor, and the firefly beside it
  screen(x) {
    x.strokeStyle = x.fillStyle = ICE;
    x.lineWidth = 5;
    rr(x, 66, 22, 508, 310, 20);
    x.stroke();
    line(x, 320, 332, 320, 372);
    line(x, 236, 374, 404, 374);
    x.lineWidth = 3;
    rr(x, 118, 64, 404, 228, 12);
    x.stroke();
    line(x, 118, 96, 522, 96);
    line(x, 222, 96, 222, 292);
    [124, 150, 176, 202].forEach((y) => line(x, 138, y, 202, y));
    x.lineWidth = 8;
    line(x, 246, 126, 396, 126);
    x.lineWidth = 3;
    rr(x, 246, 148, 250, 30, 8);
    x.stroke();
    rr(x, 246, 188, 250, 30, 8);
    x.stroke();
    rr(x, 246, 236, 88, 32, 9);
    x.fill();
    arrow(x, 372, 228, 2.1);
    x.fill();
    x.fillStyle = x.strokeStyle = GOLD;
    x.beginPath();
    x.ellipse(430, 262, 10, 12, 0, 0, 7);
    x.fill();
    x.lineWidth = 2.5;
    x.beginPath();
    x.arc(430, 262, 22, 0, 7);
    x.stroke();
  },
  // what Pyxi reads: every control a wireframe, the secret a solid bar, under a gold lens
  scan(x) {
    x.strokeStyle = x.fillStyle = ICE;
    x.lineWidth = 4;
    rr(x, 86, 30, 468, 340, 18);
    x.stroke();
    line(x, 86, 76, 554, 76);
    x.lineWidth = 3;
    const box = (a, b, w, h) => {
      x.strokeRect(a, b, w, h);
      const t = 9;
      x.lineWidth = 5;
      [
        [a, b, 1, 1],
        [a + w, b, -1, 1],
        [a, b + h, 1, -1],
        [a + w, b + h, -1, -1],
      ].forEach(([px, py, sx, sy]) => {
        x.beginPath();
        x.moveTo(px, py + sy * t);
        x.lineTo(px, py);
        x.lineTo(px + sx * t, py);
        x.stroke();
      });
      x.lineWidth = 3;
    };
    [102, 142, 182, 222].forEach((y) => box(108, y, 122, 28));
    box(262, 102, 268, 46);
    box(262, 162, 268, 46);
    box(262, 226, 116, 42);
    x.fillRect(262, 296, 210, 34); // the password field: a black bar
    x.strokeStyle = GOLD;
    x.lineWidth = 5;
    x.beginPath();
    x.arc(338, 214, 122, 0, 7);
    x.stroke();
    x.lineWidth = 3;
    for (let i = 0; i < 24; i++) {
      const a = (i / 24) * Math.PI * 2;
      const r0 = 130;
      const r1 = i % 6 === 0 ? 146 : 138;
      line(
        x,
        338 + Math.cos(a) * r0,
        214 + Math.sin(a) * r0,
        338 + Math.cos(a) * r1,
        214 + Math.sin(a) * r1,
      );
    }
  },
  // an Allow card: nothing leaves, and nothing is clicked, until you press it
  allow(x) {
    x.strokeStyle = x.fillStyle = ICE;
    x.lineWidth = 4;
    rr(x, 84, 40, 472, 320, 30);
    x.stroke();
    x.lineWidth = 7;
    line(x, 126, 96, 392, 96);
    x.lineWidth = 3.5;
    line(x, 126, 136, 500, 136);
    line(x, 126, 164, 430, 164);
    rr(x, 372, 220, 150, 92, 46);
    x.stroke();
    line(x, 412, 266, 482, 266);
    x.strokeStyle = x.fillStyle = GOLD;
    x.lineWidth = 7;
    rr(x, 118, 220, 232, 92, 46);
    x.stroke();
    x.textBaseline = "middle";
    x.textAlign = "center";
    x.font = `800 54px ${FONT}`;
    x.fillText("Allow", 234, 268);
    x.fillStyle = ICE;
    arrow(x, 318, 290, 3.6);
    x.fill();
  },
  // Pyxi itself, seen from above: head and antennae, two open wings, and the lantern of a tail
  firefly(x) {
    x.translate(318, 196);
    x.rotate((-26 * Math.PI) / 180);
    x.lineCap = x.lineJoin = "round";
    x.strokeStyle = x.fillStyle = ICE;
    // wings: long teardrops, open, each with a vein
    for (const s of [-1, 1]) {
      x.lineWidth = 4.5;
      x.beginPath();
      x.moveTo(s * 18, -42);
      x.bezierCurveTo(s * 86, -112, s * 196, -52, s * 170, 26);
      x.bezierCurveTo(s * 150, 74, s * 62, 26, s * 26, -8);
      x.closePath();
      x.stroke();
      x.lineWidth = 2.5;
      x.beginPath();
      x.moveTo(s * 26, -30);
      x.quadraticCurveTo(s * 96, -52, s * 150, 14);
      x.stroke();
      // antennae
      x.lineWidth = 4;
      x.beginPath();
      x.moveTo(s * 9, -92);
      x.quadraticCurveTo(s * 20, -132, s * 52, -146);
      x.stroke();
    }
    // head, and the thorax as an outline so it doesn't burn out to a white blob
    x.beginPath();
    x.arc(0, -76, 19, 0, 7);
    x.fill();
    x.lineWidth = 6;
    x.beginPath();
    x.ellipse(0, -28, 34, 30, 0, 0, 7);
    x.stroke();
    x.lineWidth = 3;
    line(x, 0, -52, 0, -4);
    // the lantern: a gold tail in three segments
    x.strokeStyle = x.fillStyle = GOLD;
    x.beginPath();
    x.ellipse(0, 74, 44, 76, 0, 0, 7);
    x.fill();
    x.globalCompositeOperation = "destination-out";
    x.lineWidth = 13;
    for (const y of [40, 80, 116]) {
      x.beginPath();
      x.moveTo(-50, y);
      x.quadraticCurveTo(0, y + 14, 50, y);
      x.stroke();
    }
    x.globalCompositeOperation = "source-over";
    // its light: loose sparks around the tail, thinning with distance (not rings)
    let seed = 7;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
    for (let i = 0; i < 300; i++) {
      const a = rnd() * Math.PI * 2;
      const r = 66 + Math.pow(rnd(), 1.8) * 92;
      x.fillRect(Math.cos(a) * r * 0.86, 78 + Math.sin(a) * r, 2, 2);
    }
  },
};
/** Draw a shape on a 640 x 400 canvas and return its pixels as points: [x, y, z, gold, spin]. */
function sampled(name, height) {
  const W = 640;
  const H = 400;
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const x = c.getContext("2d", { willReadFrequently: true });
  (typeof name === "function" ? name : DRAW[name])(x);
  const d = x.getImageData(0, 0, W, H).data;
  const out = [];
  for (let py = 0; py < H; py++)
    for (let px = 0; px < W; px++) {
      const i = (py * W + px) * 4;
      if (d[i + 3] < 110) continue;
      out.push(
        ((px - W / 2) / H) * height,
        ((H / 2 - py) / H) * height,
        0,
        d[i] > d[i + 2] ? 1 : 0,
        0,
      );
    }
  return out;
}

// ------------------------------------------------------------------ shapes, straight in 3D
/** A galaxy of fireflies: three loose arms on a disc. Inner ones turn faster. */
function vortex(radius) {
  const out = [];
  for (let i = 0; i < 9000; i++) {
    const r = 0.26 + Math.pow(rand(), 0.7);
    const arm = Math.floor(rand() * 3);
    const a =
      rand() < 0.72
        ? arm * ((Math.PI * 2) / 3) + r * 3.4 + gauss() * 0.42
        : rand() * Math.PI * 2;
    out.push(
      Math.cos(a) * r * radius,
      gauss() * 0.05 * radius * (1.3 - r * 0.6),
      Math.sin(a) * r * radius,
      rand() < 0.74 ? 1 : 0,
      0.34 / (0.25 + r),
    );
  }
  return out;
}
/** A chip taken apart: four plates lifted off each other, struts between, solder balls below. */
function build(s) {
  const out = [];
  const plates = [
    { h: 0.5, y: -0.34, grid: 5, gold: 0 },
    { h: 0.42, y: -0.11, grid: 4, gold: 0 },
    { h: 0.31, y: 0.12, grid: 3, gold: 0 },
    { h: 0.2, y: 0.35, grid: 0, gold: 1 },
  ];
  const push = (x, y, z, g) => out.push(x * s, y * s, z * s, g, 1);
  plates.forEach((p, k) => {
    const n = 900;
    for (let i = 0; i < n; i++) {
      // the edge of the plate
      const t = rand() * 2 - 1;
      const side = Math.floor(rand() * 4);
      const [px, pz] =
        side === 0
          ? [t, 1]
          : side === 1
            ? [t, -1]
            : side === 2
              ? [1, t]
              : [-1, t];
      push(px * p.h, p.y + (rand() < 0.5 ? 0 : 0.03), pz * p.h, p.gold);
    }
    for (let g = 1; g < p.grid; g++)
      for (let i = 0; i < 90; i++) {
        const t = (rand() * 2 - 1) * p.h;
        const o = (g / p.grid) * 2 * p.h - p.h;
        push(t, p.y, o, 0);
        push(o, p.y, t, 0);
      }
    if (p.gold)
      for (let i = 0; i < 1500; i++)
        push((rand() * 2 - 1) * p.h, p.y + 0.03, (rand() * 2 - 1) * p.h, 1); // the die, solid
    const next = plates[k + 1];
    if (next)
      for (const [cx, cz] of [
        [1, 1],
        [1, -1],
        [-1, 1],
        [-1, -1],
      ])
        for (let i = 0; i < 46; i++)
          if (i % 3)
            push(cx * next.h, p.y + ((next.y - p.y) * i) / 46, cz * next.h, 0);
  });
  for (let a = -4; a <= 4; a++)
    for (let b = -4; b <= 4; b++)
      for (let i = 0; i < 9; i++)
        push(
          a * 0.11 + gauss() * 0.012,
          -0.42 + gauss() * 0.012,
          b * 0.11 + gauss() * 0.012,
          1,
        );
  return out;
}

/** Any word, spelled in fireflies: mostly gold, with ice glinting through. */
function word(text) {
  return (x) => {
    x.textAlign = "center";
    x.textBaseline = "middle";
    x.font = `800 200px ${FONT}`;
    const size = Math.min(
      230,
      (200 * 590) / Math.max(1, x.measureText(text).width),
    );
    x.font = `800 ${size.toFixed(0)}px ${FONT}`;
    x.fillStyle = GOLD;
    x.fillText(text, 320, 205);
    // ice along the top edge of every letter, like light catching it
    x.globalCompositeOperation = "source-atop";
    x.fillStyle = ICE;
    x.fillRect(0, 205 - size * 0.46, 640, size * 0.2);
  };
}

// ------------------------------------------------------------------ the scene for each name
/** box is the part of the viewport the shape should fill, in world units; without one it's centred. */
function scene(name, aspect, box) {
  const fitW = box ? box.w : aspect * 2 * 0.9;
  const fitH = box ? box.h : 1.1;
  const flat = (h) => Math.min(h, (fitW * 0.96) / 1.6); // a 1.6:1 drawing must also fit the width
  const at = {
    cx: box ? box.cx : 0,
    cy: box ? box.cy : 0.27,
    cz: 0,
    tilt: 0,
    roll: 0,
    spin: 0,
    bright: 1,
    depth: 0,
  };
  if (name.startsWith("text:"))
    return {
      ...at,
      pts: sampled(word(name.slice(5)), flat(fitH * 1.9)),
      depth: 0.06,
    };
  switch (name) {
    case "vortex":
      return aspect > 1.15
        ? {
            ...at,
            pts: vortex(Math.min(1.25, aspect * 0.72)),
            cx: box ? box.cx : aspect * 0.48,
            cy: box ? box.cy : 0.06,
            cz: -0.5,
            tilt: 1.02,
            roll: -0.2,
            spin: 0.16,
            bright: 0.85,
          }
        : {
            ...at,
            pts: vortex(0.62),
            cy: box ? box.cy : -0.3,
            cz: -0.8,
            tilt: 1.1,
            roll: -0.2,
            spin: 0.16,
            bright: 0.5,
          };
    case "build":
      return {
        ...at,
        pts: build(Math.min(fitH / 1.42, fitW / 1.7, 1.1)),
        tilt: 0.5,
        spin: 0.45,
      };
    case "firefly":
      return {
        ...at,
        pts: sampled("firefly", flat(fitH * 1.3)),
        depth: 0.02,
        bright: 0.95,
        grain: 0.56,
      };
    case "bubble":
    case "screen":
    case "scan":
    case "allow":
      return {
        ...at,
        pts: sampled(name, flat(fitH * 0.94)),
        depth: 0.03,
        grain: 0.8,
      };
    default:
      return { ...at, pts: null, cx: 0, cy: 0, bright: 0.4 };
  }
}

const VERT = `
attribute vec3 aPos;
attribute vec2 aMeta;   // gold (0..1), brightness
attribute vec3 aStatic; // size (fraction of the viewport height), phase, 1 if it forms shapes
uniform float uAspect, uTime, uPx, uGrain;
uniform vec2 uRot;
varying float vGold;
varying float vA;
void main() {
  vec3 p = aPos;
  float cy = cos(uRot.x), sy = sin(uRot.x);
  p = vec3(p.x * cy + p.z * sy, p.y, -p.x * sy + p.z * cy);
  float cx = cos(uRot.y), sx = sin(uRot.y);
  p = vec3(p.x, p.y * cx - p.z * sx, p.y * sx + p.z * cx);
  float zc = ${CAM.toFixed(1)} - p.z;
  float f = ${CAM.toFixed(1)} / max(zc, 0.2);
  gl_Position = zc < 0.25 ? vec4(2.0, 2.0, 2.0, 1.0) : vec4(p.x * f / uAspect, p.y * f, 0.0, 1.0);
  float tw = 0.72 + 0.28 * sin(uTime * 1.9 + aStatic.y * 6.2832);
  gl_PointSize = max(1.5, aStatic.x * mix(1.0, uGrain, aStatic.z) * f * uPx);
  vGold = aMeta.x;
  vA = aMeta.y * tw * clamp(f, 0.35, 1.5);
}`;
const FRAG = `
precision mediump float;
uniform float uOpacity;
varying float vGold;
varying float vA;
void main() {
  float d = length(gl_PointCoord - 0.5) * 2.0;
  float a = smoothstep(1.0, 0.0, d);
  a = a * a * vA * uOpacity;
  vec3 col = mix(vec3(0.62, 0.80, 1.0), vec3(1.0, 0.80, 0.42), vGold);
  gl_FragColor = vec4(col * a, a);
}`;

export function initSwarm({ reduced = false } = {}) {
  const canvas = document.querySelector("canvas.swarm");
  if (!canvas) return null;
  const gl = canvas.getContext("webgl", {
    alpha: true,
    antialias: false,
    premultipliedAlpha: true,
    powerPreference: "high-performance",
  });
  if (!gl) {
    canvas.remove();
    return null;
  }
  const small = innerWidth < 760 || matchMedia("(pointer: coarse)").matches;
  const N = small ? 5200 : 13000;
  const NS = Math.round(N * 0.72); // these form the shapes; the rest is dust that keeps its place

  const sh = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return s;
  };
  const prog = gl.createProgram();
  gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT));
  gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    canvas.remove();
    return null;
  }
  gl.useProgram(prog);
  const U = Object.fromEntries(
    ["uAspect", "uTime", "uPx", "uRot", "uOpacity", "uGrain"].map((n) => [
      n,
      gl.getUniformLocation(prog, n),
    ]),
  );

  const pos = new Float32Array(N * 3);
  const vel = new Float32Array(N * 3);
  const loc = new Float32Array(N * 3); // where each point belongs, in its shape's own frame
  const meta = new Float32Array(N * 2); // gold, brightness (eased toward the two below)
  const goldT = new Float32Array(N);
  const briT = new Float32Array(N);
  const spinW = new Float32Array(N);
  const stat = new Float32Array(N * 3);
  const phase = new Float32Array(N);
  const stiff = new Float32Array(N);
  const wander = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    phase[i] = rand();
    stiff[i] = 0.018 + rand() * 0.05;
    const bokeh = i >= NS && rand() < 0.05; // a few big soft ones, far out of focus
    stat[i * 3] = bokeh
      ? 0.022 + rand() * 0.02
      : 0.0042 + Math.pow(rand(), 2.2) * 0.0085;
    stat[i * 3 + 1] = phase[i];
    stat[i * 3 + 2] = i < NS ? 1 : 0;
    wander[i] = i < NS ? 0.006 : 0.03;
    if (i >= NS) {
      goldT[i] = rand() < 0.7 ? 1 : 0;
      briT[i] = bokeh ? 0.1 : 0.25 + rand() * 0.4;
    }
  }
  const buffer = (data, usage, attr, size) => {
    const b = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, data, usage);
    const l = gl.getAttribLocation(prog, attr);
    gl.enableVertexAttribArray(l);
    gl.vertexAttribPointer(l, size, gl.FLOAT, false, 0, 0);
    return b;
  };
  const posBuf = buffer(pos, gl.DYNAMIC_DRAW, "aPos", 3);
  const metaBuf = buffer(meta, gl.DYNAMIC_DRAW, "aMeta", 2);
  buffer(stat, gl.STATIC_DRAW, "aStatic", 3);
  gl.disable(gl.DEPTH_TEST);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE);
  gl.clearColor(0, 0, 0, 0);

  let aspect = 1;
  let name = "";
  let shape = scene("field", 1);
  let anchor = null; // the element on the page the shape sits in, if the cue names one
  const boxOf = (el) => {
    const r = el.getBoundingClientRect();
    return {
      cx: (((r.left + r.width / 2) / innerWidth) * 2 - 1) * aspect,
      cy: 1 - ((r.top + r.height / 2) / innerHeight) * 2,
      w: (r.width / innerWidth) * 2 * aspect,
      h: (r.height / innerHeight) * 2,
    };
  };
  /** The shape rides its box as the page scrolls. */
  const follow = () => {
    if (!anchor || !shape.pts) return;
    const b = boxOf(anchor);
    shape.cx = b.cx;
    shape.cy = b.cy;
  };
  let spin = 0;
  let opacity = 0;
  let opacityT = 1;
  let grain = 1;
  const dustTo = (i) => {
    loc[i * 3] = (rand() * 2 - 1) * aspect * 1.45;
    loc[i * 3 + 1] = (rand() * 2 - 1) * 1.35;
    loc[i * 3 + 2] = -3.6 + rand() * 4.8;
  };
  /** Send the shape's points to their new places. */
  const assign = (kick) => {
    const p = shape.pts;
    const count = p ? p.length / 5 : 0;
    for (let i = 0; i < NS; i++) {
      if (count) {
        const k = Math.floor(rand() * count) * 5;
        loc[i * 3] = p[k];
        loc[i * 3 + 1] = p[k + 1];
        loc[i * 3 + 2] = p[k + 2] + (rand() - 0.5) * (shape.depth || 0);
        goldT[i] = p[k + 3];
        spinW[i] = p[k + 4];
        briT[i] = shape.bright * (0.7 + rand() * 0.3);
        wander[i] = 0.005;
      } else {
        dustTo(i);
        loc[i * 3 + 2] = -3.8 + rand() * 3; // the field sits further back than the dust
        goldT[i] = rand() < 0.7 ? 1 : 0;
        spinW[i] = 0;
        briT[i] = shape.bright * (0.35 + rand() * 0.65);
        wander[i] = 0.03;
      }
      if (kick) {
        // leave in a swirl, not a straight line
        const j = i * 3;
        vel[j] += -pos[j + 1] * 0.012 + (rand() - 0.5) * 0.02;
        vel[j + 1] += pos[j] * 0.012 + (rand() - 0.5) * 0.02;
        vel[j + 2] += (rand() - 0.5) * 0.05;
      }
    }
  };
  const set = (next, { force = false } = {}) => {
    if (next === name && !force) return;
    name = next;
    shape = scene(name, aspect, anchor ? boxOf(anchor) : null);
    spin = 0;
    assign(!reduced && !force);
    if (reduced) snap();
  };

  // ---- pointer, scroll, size
  let pointer = null;
  let rx = 0;
  let ry = 0;
  let lift = 0; // a nudge from scrolling
  let lastY = scrollY;
  if (!reduced && matchMedia("(hover: hover) and (pointer: fine)").matches) {
    addEventListener(
      "pointermove",
      (e) =>
        (pointer = {
          x: (e.clientX / innerWidth) * 2 - 1,
          y: 1 - (e.clientY / innerHeight) * 2,
        }),
      { passive: true },
    );
    document.documentElement.addEventListener(
      "pointerleave",
      () => (pointer = null),
    );
  }
  // a click sends a shockwave through whatever is near it
  let burst = null;
  if (!reduced)
    addEventListener(
      "pointerdown",
      (e) =>
        (burst = {
          x: ((e.clientX / innerWidth) * 2 - 1) * aspect,
          y: 1 - (e.clientY / innerHeight) * 2,
        }),
      { passive: true },
    );
  const cues = [...document.querySelectorAll("[data-swarm]")].map((el) => ({
    el,
    at: el.dataset.swarmAt ? document.querySelector(el.dataset.swarmAt) : null,
  }));
  const refresh = () => {
    const mid = innerHeight * 0.5;
    let next = "field";
    let op = 0.5;
    let at = null;
    for (const cue of cues) {
      const r = cue.el.getBoundingClientRect();
      if (r.top <= mid && r.bottom >= mid) {
        next = cue.el.dataset.swarm || "field";
        op = Number(cue.el.dataset.swarmOpacity || 1);
        at = cue.at;
        break;
      }
    }
    opacityT = op;
    const moved = at !== anchor;
    anchor = at;
    set(next, { force: moved && next === name });
    if (reduced && anchor) {
      follow();
      snap();
    }
  };
  let queued = 0;
  addEventListener(
    "scroll",
    () => {
      lift = Math.max(
        -0.02,
        Math.min(0.02, lift + (scrollY - lastY) * 0.00005),
      );
      lastY = scrollY;
      queued ||= requestAnimationFrame(() => {
        queued = 0;
        refresh();
      });
    },
    { passive: true },
  );
  const fit = () => {
    const dpr = Math.min(small ? 1.5 : 1.75, devicePixelRatio || 1);
    const w = innerWidth;
    const h = innerHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    gl.viewport(0, 0, canvas.width, canvas.height);
    aspect = w / h;
    gl.uniform1f(U.uAspect, aspect);
    gl.uniform1f(U.uPx, canvas.height);
    for (let i = NS; i < N; i++) dustTo(i);
    if (name) set(name, { force: true });
  };
  let resizeTimer = 0;
  addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      fit();
      if (reduced) snap();
    }, 150);
  });

  // ---- the frame
  /** Where point i belongs right now, written into out[0..2]. */
  const out = [0, 0, 0];
  const home = (i, t) => {
    const j = i * 3;
    let x = loc[j];
    let y = loc[j + 1];
    let z = loc[j + 2];
    if (i < NS && shape.pts) {
      const w = spinW[i];
      if (w) {
        const a = spin * w;
        const c = Math.cos(a);
        const s = Math.sin(a);
        const x2 = x * c + z * s;
        z = -x * s + z * c;
        x = x2;
      }
      if (shape.tilt) {
        const c = Math.cos(shape.tilt);
        const s = Math.sin(shape.tilt);
        const y2 = y * c - z * s;
        z = y * s + z * c;
        y = y2;
      }
      if (shape.roll) {
        const c = Math.cos(shape.roll);
        const s = Math.sin(shape.roll);
        const x2 = x * c - y * s;
        y = x * s + y * c;
        x = x2;
      }
      x += shape.cx;
      y += shape.cy;
      z += shape.cz;
    }
    const wa = wander[i];
    const ph = phase[i];
    out[0] = x + Math.sin(t * 0.6 + ph * 40) * wa;
    out[1] = y + Math.cos(t * 0.47 + ph * 61) * wa;
    out[2] = z;
  };
  const draw = (t) => {
    gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, pos);
    gl.bindBuffer(gl.ARRAY_BUFFER, metaBuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, meta);
    gl.uniform1f(U.uTime, t);
    gl.uniform2f(U.uRot, ry, rx);
    gl.uniform1f(U.uOpacity, opacity);
    gl.uniform1f(U.uGrain, grain);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.POINTS, 0, N);
  };
  /** Everything in place at once: the first frame, and every frame with reduced motion. */
  function snap() {
    for (let i = 0; i < N; i++) {
      home(i, 0);
      pos[i * 3] = out[0];
      pos[i * 3 + 1] = out[1];
      pos[i * 3 + 2] = out[2];
      meta[i * 2] = goldT[i];
      meta[i * 2 + 1] = briT[i];
    }
    opacity = opacityT;
    grain = shape.grain || 1;
    draw(0);
  }
  let last = 0;
  const tick = (ms) => {
    requestAnimationFrame(tick);
    if (document.hidden) return;
    const t = ms / 1000;
    const k = Math.min(2.5, ((ms - last) / 1000) * 60 || 1);
    follow();
    last = ms;
    spin += shape.spin * (k / 60);
    opacity += (opacityT - opacity) * 0.05 * k;
    grain += ((shape.grain || 1) - grain) * 0.05 * k;
    const tx = pointer ? pointer.x : 0;
    const ty = pointer ? pointer.y : 0;
    ry += (Math.sin(t * 0.21) * 0.1 + tx * 0.12 - ry) * 0.04 * k;
    rx += (Math.sin(t * 0.16) * 0.03 - ty * 0.06 - rx) * 0.04 * k;
    const px = tx * aspect;
    const py = ty;
    const R2 = 0.05;
    const damp = Math.pow(0.86, k);
    const ease = Math.min(1, 0.07 * k);
    for (let i = 0; i < N; i++) {
      const j = i * 3;
      home(i, t);
      let vx = vel[j] + (out[0] - pos[j]) * stiff[i] * k;
      let vy =
        vel[j + 1] +
        (out[1] - pos[j + 1]) * stiff[i] * k +
        lift * (0.3 + phase[i]);
      let vz = vel[j + 2] + (out[2] - pos[j + 2]) * stiff[i] * k;
      if (pointer) {
        // the swarm parts around your cursor
        const dx = pos[j] - px;
        const dy = pos[j + 1] - py;
        const d2 = dx * dx + dy * dy;
        if (d2 < R2 && d2 > 1e-6 && pos[j + 2] > -1.2) {
          const f = ((1 - d2 / R2) * 0.006 * k) / Math.sqrt(d2);
          vx += dx * f;
          vy += dy * f;
        }
      }
      if (burst) {
        const dx = pos[j] - burst.x;
        const dy = pos[j + 1] - burst.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 0.5) {
          const f = (0.05 * (1 - d2 / 0.5)) / (Math.sqrt(d2) + 0.08);
          vx += dx * f;
          vy += dy * f;
          vz += (rand() - 0.3) * 0.12 * (1 - d2 / 0.5);
        }
      }
      vx *= damp;
      vy *= damp;
      vz *= damp;
      vel[j] = vx;
      vel[j + 1] = vy;
      vel[j + 2] = vz;
      pos[j] += vx * k;
      pos[j + 1] += vy * k;
      pos[j + 2] += vz * k;
      meta[i * 2] += (goldT[i] - meta[i * 2]) * ease;
      meta[i * 2 + 1] += (briT[i] - meta[i * 2 + 1]) * ease;
    }
    burst = null;
    lift *= Math.pow(0.8, k);
    draw(t);
  };

  fit();
  refresh();
  if (!name) set("field");
  // start scattered wide, then fly in
  for (let i = 0; i < N; i++) {
    pos[i * 3] = (rand() * 2 - 1) * aspect * 2.2;
    pos[i * 3 + 1] = (rand() * 2 - 1) * 2;
    pos[i * 3 + 2] = -4 + rand() * 4;
  }
  if (reduced) snap();
  else requestAnimationFrame(tick);
  // shapes with words in them are redrawn once the font is in
  document.fonts?.ready.then(
    () =>
      (name === "allow" || name.startsWith("text:")) &&
      set(name, { force: true }),
  );
  return { set, refresh };
}
