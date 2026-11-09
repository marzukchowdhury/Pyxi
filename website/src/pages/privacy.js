/**
 * The privacy page.
 *
 * "What leaves your computer": a canvas scene of the one journey the policy describes. Your PC
 * on the left (screen, files, accounts, stacked like plates of glass), a thread of light to the
 * AI model on the right, and packets travelling along it. In between stands the gate: anything
 * that looks like a password field, a key, a banking app or a card number hits it, turns into a
 * black bar and falls back. It never crosses. A dim dashed path carries what stays on this PC
 * into its own box: conversations (30 days), the Memory Palace (72 hours), encrypted tokens.
 * The whole scene tilts a little with the mouse. Reduced motion gets one composed frame.
 *
 * Also: the rendered policy has no heading ids (marked leaves them out), so the trust grid's
 * links get their anchors here.
 */
import "./privacy.css";

const W = 1600;
const H = 720;
const FONT = '"Inter Variable", Inter, "Segoe UI", sans-serif';
const CYCLE = 10; // seconds, then the packets start over

// The paths. Packets to the model follow the pipe; blocked ones stop at the gate; local ones go
// into the box. Points are in scene units.
const PIPE = { a: [520, 335], c: [930, 290], b: [1300, 350] };
const GATE = { x: 880, top: 150, bottom: 530 };
const MODEL = { x: 1400, y: 350, r: 86 };
const BOX = { x: 560, y: 568, w: 440, h: 136 };
const LOCAL = { a: [470, 480], c: [500, 640], b: [700, 640] };

const PACKETS = [
  { kind: "ok", text: "your question", at: 0 },
  { kind: "no", text: "password field", at: 0.8 },
  { kind: "local", text: "conversations · kept 30 days", at: 1.0, slot: 0 },
  { kind: "ok", text: "masked screenshot", at: 1.6 },
  { kind: "no", text: "API key", at: 2.4 },
  { kind: "local", text: "Memory Palace · kept 72 hours", at: 3.0, slot: 1 },
  { kind: "ok", text: "on-screen text", at: 3.2 },
  { kind: "no", text: "banking app", at: 4.0 },
  {
    kind: "local",
    text: "sign-in tokens · encrypted (DPAPI)",
    at: 5.0,
    slot: 2,
  },
  { kind: "ok", text: "the part of the file the job needs", at: 4.8 },
  { kind: "no", text: "card number", at: 5.6 },
];
const OK_T = 4; // seconds along the pipe
const NO_T = 2.1; // to the gate
const FALL_T = 1.4;
const LOCAL_T = 2.6;

const bez = (p, t) => {
  const u = 1 - t;
  return [
    u * u * p.a[0] + 2 * u * t * p.c[0] + t * t * p.b[0],
    u * u * p.a[1] + 2 * u * t * p.c[1] + t * t * p.b[1],
  ];
};
const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

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
  return ctx.measureText(s).width;
}
/** A plate of glass seen from above and to the side: a rhombus with a little thickness. */
function plate(ctx, cx, cy, hw, hh, depth, fill, edge) {
  ctx.fillStyle = "rgba(0,0,0,0.35)";
  ctx.beginPath();
  ctx.moveTo(cx - hw, cy + depth);
  ctx.lineTo(cx, cy + hh + depth);
  ctx.lineTo(cx + hw, cy + depth);
  ctx.lineTo(cx + hw, cy);
  ctx.lineTo(cx, cy + hh);
  ctx.lineTo(cx - hw, cy);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = fill;
  ctx.strokeStyle = edge;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(cx - hw, cy);
  ctx.lineTo(cx, cy - hh);
  ctx.lineTo(cx + hw, cy);
  ctx.lineTo(cx, cy + hh);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
}
function chip(ctx, x, y, label, style, alpha = 1) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.font = `600 21px ${FONT}`;
  const w = ctx.measureText(label).width + 30;
  const h = 38;
  if (style === "bar") {
    ctx.fillStyle = "#020306";
    rr(ctx, x - w / 2, y - h / 2, w, h, 8);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,200,97,0.8)";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = "rgba(255,200,97,0.14)";
    for (let hx = x - w / 2 + 8; hx < x + w / 2 - 8; hx += 16)
      ctx.fillRect(hx, y - h / 2 + 7, 7, h - 14);
    text(
      ctx,
      "blacked out",
      x,
      y + h / 2 + 16,
      `600 14px ${FONT}`,
      "rgba(255,217,140,0.9)",
      "center",
    );
  } else {
    const gold = style === "ok" || style === "no";
    ctx.shadowColor = gold ? "rgba(255,200,97,0.45)" : "rgba(158,203,255,0.35)";
    ctx.shadowBlur = 24;
    ctx.fillStyle = gold ? "rgba(46,36,14,0.92)" : "rgba(14,22,34,0.92)";
    rr(ctx, x - w / 2, y - h / 2, w, h, 10);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = gold ? "rgba(255,200,97,0.7)" : "rgba(158,203,255,0.55)";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    text(
      ctx,
      label,
      x,
      y + 1,
      `600 21px ${FONT}`,
      gold ? "#ffe4a8" : "#dbeaff",
      "center",
    );
  }
  ctx.restore();
  return w;
}

function drawScene(ctx, t, par) {
  const px = par.x;
  const py = par.y;
  ctx.clearRect(0, 0, W, H);

  // ---- far layer: the grid of dots and the floor glow
  ctx.save();
  ctx.translate(px * 6, py * 4);
  ctx.fillStyle = "rgba(158,203,255,0.08)";
  for (let y = 40; y < H; y += 60)
    for (let x = 40; x < W; x += 60) ctx.fillRect(x, y, 2, 2);
  const floor = ctx.createRadialGradient(800, 760, 40, 800, 760, 760);
  floor.addColorStop(0, "rgba(158,203,255,0.08)");
  floor.addColorStop(1, "rgba(158,203,255,0)");
  ctx.fillStyle = floor;
  ctx.fillRect(0, 300, W, H - 300);
  ctx.restore();

  // ---- mid layer: the PC, the pipe, the gate, the model, the box
  ctx.save();
  ctx.translate(px * 14, py * 10);

  // the pipe: a glass tube with a bright core
  ctx.lineCap = "round";
  ctx.strokeStyle = "rgba(255,200,97,0.07)";
  ctx.lineWidth = 54;
  ctx.beginPath();
  ctx.moveTo(...PIPE.a);
  ctx.quadraticCurveTo(...PIPE.c, ...PIPE.b);
  ctx.stroke();
  ctx.strokeStyle = "rgba(255,200,97,0.16)";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.strokeStyle = "rgba(255,200,97,0.45)";
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 14]);
  ctx.lineDashOffset = -t * 60;
  ctx.stroke();
  ctx.setLineDash([]);
  text(
    ctx,
    "to the AI model, to work out the reply",
    760,
    232,
    `500 15px ${FONT}`,
    "rgba(255,217,140,0.7)",
    "center",
  );

  // the local path: dim and dashed, down into the box
  ctx.strokeStyle = "rgba(158,203,255,0.35)";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([6, 8]);
  ctx.beginPath();
  ctx.moveTo(...LOCAL.a);
  ctx.quadraticCurveTo(...LOCAL.c, ...LOCAL.b);
  ctx.stroke();
  ctx.setLineDash([]);

  // the gate
  const gg = ctx.createLinearGradient(GATE.x - 40, 0, GATE.x + 40, 0);
  gg.addColorStop(0, "rgba(158,203,255,0)");
  gg.addColorStop(0.5, "rgba(158,203,255,0.16)");
  gg.addColorStop(1, "rgba(158,203,255,0)");
  ctx.fillStyle = gg;
  ctx.fillRect(GATE.x - 40, GATE.top, 80, GATE.bottom - GATE.top);
  ctx.save();
  ctx.shadowColor = "rgba(158,203,255,0.8)";
  ctx.shadowBlur = 18;
  ctx.strokeStyle = "rgba(190,220,255,0.9)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(GATE.x, GATE.top);
  ctx.lineTo(GATE.x, GATE.bottom);
  ctx.stroke();
  ctx.restore();
  // the shield mark on the gate
  ctx.save();
  ctx.translate(GATE.x, GATE.top - 46);
  ctx.fillStyle = "rgba(10,14,22,0.95)";
  ctx.strokeStyle = "rgba(158,203,255,0.8)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(0, -22);
  ctx.lineTo(20, -13);
  ctx.lineTo(18, 6);
  ctx.quadraticCurveTo(10, 20, 0, 26);
  ctx.quadraticCurveTo(-10, 20, -18, 6);
  ctx.lineTo(-20, -13);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.strokeStyle = "#eaf3ff";
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.moveTo(-8, 1);
  ctx.lineTo(-2, 7);
  ctx.lineTo(9, -6);
  ctx.stroke();
  ctx.restore();
  text(
    ctx,
    "Masked before anything leaves",
    GATE.x,
    GATE.top - 96,
    `600 17px ${FONT}`,
    "#eaf3ff",
    "center",
  );
  text(
    ctx,
    "password fields, keys and tokens, banking apps, card numbers",
    GATE.x,
    GATE.top - 74,
    `400 14px ${FONT}`,
    "rgba(232,238,248,0.55)",
    "center",
  );

  // the model: a glass hexagon that breathes
  const pulse = 1 + Math.sin(t * 2.2) * 0.02;
  ctx.save();
  ctx.translate(MODEL.x, MODEL.y);
  ctx.scale(pulse, pulse);
  const mg = ctx.createRadialGradient(0, 0, 10, 0, 0, MODEL.r * 1.4);
  mg.addColorStop(0, "rgba(255,200,97,0.35)");
  mg.addColorStop(0.5, "rgba(255,200,97,0.08)");
  mg.addColorStop(1, "rgba(255,200,97,0)");
  ctx.fillStyle = mg;
  ctx.beginPath();
  ctx.arc(0, 0, MODEL.r * 1.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "rgba(20,18,12,0.85)";
  ctx.strokeStyle = "rgba(255,200,97,0.7)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 + Math.PI / 6;
    ctx[i ? "lineTo" : "moveTo"](Math.cos(a) * MODEL.r, Math.sin(a) * MODEL.r);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.strokeStyle = "rgba(255,200,97,0.25)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 + Math.PI / 6;
    ctx[i ? "lineTo" : "moveTo"](
      Math.cos(a) * MODEL.r * 0.62,
      Math.sin(a) * MODEL.r * 0.62,
    );
  }
  ctx.closePath();
  ctx.stroke();
  ctx.restore();
  text(
    ctx,
    "The AI model",
    MODEL.x,
    MODEL.y - 8,
    `600 22px ${FONT}`,
    "#fff3cf",
    "center",
  );
  text(
    ctx,
    "answers, then forgets",
    MODEL.x,
    MODEL.y + 18,
    `400 14px ${FONT}`,
    "rgba(255,228,168,0.6)",
    "center",
  );
  text(
    ctx,
    "through the Pyxi beta server and OpenRouter",
    MODEL.x,
    MODEL.y + MODEL.r + 34,
    `400 14px ${FONT}`,
    "rgba(232,238,248,0.5)",
    "center",
  );
  text(
    ctx,
    "not sold, not used to train models",
    MODEL.x,
    MODEL.y + MODEL.r + 56,
    `500 14px ${FONT}`,
    "rgba(255,217,140,0.75)",
    "center",
  );

  // the box for what stays here
  ctx.strokeStyle = "rgba(158,203,255,0.5)";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([8, 7]);
  rr(ctx, BOX.x, BOX.y, BOX.w, BOX.h, 16);
  ctx.fillStyle = "rgba(158,203,255,0.04)";
  ctx.fill();
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#07080b";
  ctx.fillRect(BOX.x + 18, BOX.y - 9, 150, 18);
  text(
    ctx,
    "STAYS ON THIS PC",
    BOX.x + 26,
    BOX.y,
    `700 13px ${FONT}`,
    "rgba(158,203,255,0.85)",
  );
  text(
    ctx,
    "%APPDATA%\\Pyxi, deleted on a schedule or when you say so",
    BOX.x + 18,
    BOX.y + BOX.h + 20,
    `400 14px ${FONT}`,
    "rgba(232,238,248,0.45)",
  );

  // the blocked bars that already fell: a small heap under the gate
  ctx.fillStyle = "rgba(2,3,6,0.95)";
  ctx.strokeStyle = "rgba(255,200,97,0.35)";
  ctx.lineWidth = 1;
  [
    [GATE.x - 130, 512, 80],
    [GATE.x - 190, 526, 64],
    [GATE.x - 110, 528, 50],
  ].forEach(([x, y, w]) => {
    rr(ctx, x, y, w, 10, 3);
    ctx.fill();
    ctx.stroke();
  });
  text(
    ctx,
    "never leaves",
    GATE.x - 140,
    554,
    `500 13px ${FONT}`,
    "rgba(255,217,140,0.6)",
    "center",
  );
  ctx.restore();

  // ---- near layer: the packets
  ctx.save();
  ctx.translate(px * 22, py * 16);
  const cyc = ((t % CYCLE) + CYCLE) % CYCLE;
  for (const p of PACKETS) {
    const age = cyc - p.at;
    if (p.kind === "ok") {
      if (age < 0 || age > OK_T) continue;
      const u = ease(age / OK_T);
      const [x, y] = bez(PIPE, u);
      // melts into the model as it arrives, so the model's own label stays readable
      const fade = u > 0.78 ? (1 - u) / 0.22 : Math.min(1, age / 0.3);
      chip(ctx, x, y, p.text, "ok", fade);
    } else if (p.kind === "no") {
      if (age < 0 || age > NO_T + FALL_T) continue;
      if (age <= NO_T) {
        const u = ease(age / NO_T) * 0.5; // the gate sits halfway along the pipe
        const [x, y] = bez(PIPE, u);
        chip(ctx, x, y - 54, p.text, "no", Math.min(1, age / 0.3));
      } else {
        const f = (age - NO_T) / FALL_T;
        const [gx, gy] = bez(PIPE, 0.5);
        const x = gx - 40 - f * 110;
        const y = gy - 54 + f * f * 300;
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(-f * 0.5);
        chip(ctx, 0, 0, p.text, "bar", 1 - Math.max(0, f - 0.7) / 0.3);
        ctx.restore();
        // the flash where it hit the gate
        if (f < 0.25) {
          ctx.save();
          ctx.globalAlpha = (0.25 - f) * 3;
          ctx.fillStyle = "rgba(158,203,255,0.6)";
          ctx.fillRect(GATE.x - 3, gy - 110, 6, 120);
          ctx.restore();
        }
      }
    } else {
      if (age < 0 || age > CYCLE - p.at - 0.4) continue;
      ctx.font = `600 21px ${FONT}`;
      const w = ctx.measureText(p.text).width + 30;
      const end = [BOX.x + 22 + w / 2, BOX.y + 30 + p.slot * 40];
      if (age <= LOCAL_T) {
        const u = ease(age / LOCAL_T);
        const [x, y] = bez({ a: LOCAL.a, c: LOCAL.c, b: end }, u);
        chip(ctx, x, y, p.text, "local", Math.min(1, age / 0.3));
      } else {
        const left = CYCLE - p.at - age;
        chip(ctx, end[0], end[1], p.text, "local", Math.min(1, left / 0.4));
      }
    }
  }
  // your PC sits in front: packets come out from behind the plates
  // your PC: three plates, screen on top
  const plates = [
    [
      "Your accounts",
      "only the ones you connect",
      470,
      "rgba(158,203,255,0.08)",
    ],
    ["Your files", "only in folders you allow", 400, "rgba(158,203,255,0.1)"],
    [
      "Your screen",
      "only when you talk or type to Pyxi",
      330,
      "rgba(158,203,255,0.14)",
    ],
  ];
  plates.forEach(([name, sub, y, fill], i) => {
    plate(
      ctx,
      300,
      y,
      220,
      90,
      18,
      fill,
      i === 2 ? "rgba(158,203,255,0.75)" : "rgba(158,203,255,0.4)",
    );
    text(ctx, name, 300, y - 10, `600 22px ${FONT}`, "#eaf3ff", "center");
    text(
      ctx,
      sub,
      300,
      y + 14,
      `400 15px ${FONT}`,
      "rgba(232,238,248,0.55)",
      "center",
    );
  });
  // the screen plate carries a tiny window
  ctx.fillStyle = "rgba(255,255,255,0.05)";
  rr(ctx, 260, 236, 80, 12, 3);
  ctx.fill();
  text(
    ctx,
    "YOUR PC",
    300,
    160,
    `700 13px ${FONT}`,
    "rgba(158,203,255,0.7)",
    "center",
  );
  ctx.fillStyle = "rgba(158,203,255,0.5)";
  ctx.fillRect(270, 176, 60, 1);

  ctx.restore();
}

function startScene(root, reduced) {
  const canvas = root.querySelector("canvas.leave");
  const tilt = root.querySelector(".leave-tilt");
  const ctx = canvas?.getContext("2d");
  if (!ctx || typeof ctx.roundRect !== "function") return;
  let scale = 1;
  const fit = () => {
    const r = canvas.getBoundingClientRect();
    if (!r.width) return;
    const dpr = Math.min(2, devicePixelRatio || 1);
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(((r.width * H) / W) * dpr);
    scale = (r.width / W) * dpr;
    dirty = true;
  };
  let dirty = true;
  new ResizeObserver(fit).observe(canvas);
  fit();
  const par = { x: 0, y: 0, tx: 0, ty: 0 };
  const paint = (t) => {
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    drawScene(ctx, t, par);
  };
  if (reduced) {
    const still = () => paint(3.6);
    still();
    document.fonts?.ready.then(still);
    new ResizeObserver(still).observe(canvas);
    return;
  }
  if (matchMedia("(hover: hover) and (pointer: fine)").matches) {
    root.addEventListener("pointermove", (e) => {
      const r = root.getBoundingClientRect();
      par.tx = (e.clientX - r.left) / r.width - 0.5;
      par.ty = (e.clientY - r.top) / r.height - 0.5;
    });
    root.addEventListener("pointerleave", () => (par.tx = par.ty = 0));
  }
  let visible = false;
  new IntersectionObserver(([e]) => (visible = e.isIntersecting)).observe(
    canvas,
  );
  const t0 = performance.now();
  const tick = (now) => {
    requestAnimationFrame(tick);
    if (!visible || document.hidden) return;
    par.x += (par.tx - par.x) * 0.08;
    par.y += (par.ty - par.y) * 0.08;
    if (tilt)
      tilt.style.transform = `rotateX(${(-par.y * 4).toFixed(2)}deg) rotateY(${(par.x * 6).toFixed(2)}deg)`;
    paint((now - t0) / 1000);
  };
  requestAnimationFrame(tick);
}

/** marked leaves headings without ids, so the trust grid's links get their anchors here. */
function anchorHeadings() {
  const slug = (s) =>
    s
      .toLowerCase()
      .replace(/['’]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");
  document.querySelectorAll(".prose h2").forEach((h) => {
    if (!h.id) h.id = slug(h.textContent);
  });
  if (location.hash) {
    const target = document.getElementById(
      decodeURIComponent(location.hash.slice(1)),
    );
    target?.scrollIntoView({ block: "start" });
  }
}

if (document.body.classList.contains("page-privacy")) {
  const forceMotion = new URLSearchParams(location.search).has("motion");
  const reduced =
    !forceMotion && matchMedia("(prefers-reduced-motion: reduce)").matches;
  anchorHeadings();
  const scene = document.querySelector(".leave-scene");
  if (scene) startScene(scene, reduced);
}
