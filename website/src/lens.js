/**
 * "How it works" as a lens you hold. The settings window is drawn here, sharp at any size, and
 * the page's own firefly is the instrument: wherever it hovers over the window, the screen turns
 * to glass and you see the layer Pyxi reads, straight from Windows UI Automation. Every control
 * is a wireframe with its role, name, state and position; the password field is a black bar,
 * because that's what Pyxi sends: nothing.
 *
 *   step 1  your screen, as it is: the lens is away;
 *   step 2  what Pyxi reads: the lens roams from control to control, or rides your firefly;
 *   step 3  what Pyxi draws: the firefly threads light to Export and rings it, on top.
 * Hovering a numbered card holds its layer; card 2 turns the whole window to glass.
 */
const CW = 2048;
const CH = 1280;
const SCREEN = 0.625; // canvas px to the "real" 1280 x 800 screen the readouts quote
const FONT = '"Inter Variable", Inter, "Segoe UI", sans-serif';
const MONO = 'ui-monospace, "Cascadia Code", Consolas, monospace';

// What UI Automation gives Pyxi for this window: a box, a role, a name and a state.
const CONTROLS = [
  { r: [28, 168, 464, 68], role: "ListItem", name: "General" },
  {
    r: [28, 248, 464, 68],
    role: "ListItem",
    name: "Appearance",
    state: "selected",
  },
  { r: [28, 328, 464, 68], role: "ListItem", name: "Privacy" },
  { r: [28, 408, 464, 68], role: "ListItem", name: "Advanced" },
  { r: [600, 330, 1360, 104], role: "Toggle", name: "Dark mode", state: "on" },
  {
    r: [600, 452, 1360, 104],
    role: "Toggle",
    name: "Reduce motion",
    state: "off",
  },
  { r: [600, 640, 272, 84], role: "Button", name: "Export", target: true },
  {
    r: [600, 860, 760, 72],
    role: "PasswordBox",
    name: "Sync passphrase",
    secret: true,
  },
];
const ROAM = [1, 4, 6, 7, 5, 0, 2, 3]; // the order the lens visits them on its own

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

// ------------------------------------------------------------------ layer 1: the window
function drawUI(ctx) {
  const bg = ctx.createLinearGradient(0, 0, 0, CH);
  bg.addColorStop(0, "#151922");
  bg.addColorStop(1, "#0b0d12");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, CW, CH);
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
    text(
      ctx,
      t,
      106,
      y + 35,
      `${on ? 600 : 500} 34px ${FONT}`,
      on ? "#eaf3ff" : "rgba(232,238,248,0.72)",
    );
  });
  text(ctx, "Appearance", 600, 208, `700 62px ${FONT}`, "#f4f7fb");
  text(
    ctx,
    "How everything looks on this PC.",
    600,
    270,
    `400 32px ${FONT}`,
    "rgba(232,238,248,0.5)",
  );
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
  text(
    ctx,
    "Saves your theme to one file.",
    906,
    683,
    `400 28px ${FONT}`,
    "rgba(232,238,248,0.45)",
  );
  // a password field, so the lens has a secret to keep
  text(
    ctx,
    "Sync passphrase",
    600,
    822,
    `500 28px ${FONT}`,
    "rgba(232,238,248,0.6)",
  );
  ctx.fillStyle = "rgba(255,255,255,0.05)";
  rr(ctx, 600, 860, 760, 72, 16);
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.1)";
  ctx.lineWidth = 2;
  ctx.stroke();
  text(
    ctx,
    "••••••••••••",
    640,
    898,
    `700 30px ${FONT}`,
    "rgba(232,238,248,0.75)",
  );
  ctx.strokeStyle = "rgba(232,238,248,0.4)";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.ellipse(1312, 896, 18, 11, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(1312, 896, 5, 0, Math.PI * 2);
  ctx.stroke();
  text(
    ctx,
    "Used when you sync your theme between PCs.",
    1392,
    896,
    `400 26px ${FONT}`,
    "rgba(232,238,248,0.4)",
  );
  ctx.fillStyle = "rgba(255,255,255,0.05)";
  [
    [600, 1010, 900],
    [600, 1060, 640],
  ].forEach(([x, y, w]) => {
    rr(ctx, x, y, w, 20, 10);
    ctx.fill();
  });
}

// ------------------------------------------------------------------ layer 2: what Pyxi reads
const px = (v) => Math.round(v * SCREEN);
function drawReads(ctx) {
  drawUI(ctx);
  ctx.fillStyle = "rgba(6,8,13,0.93)";
  ctx.fillRect(0, 0, CW, CH);
  ctx.fillStyle = "rgba(158,203,255,0.11)";
  for (let y = 32; y < CH; y += 64)
    for (let x = 32; x < CW; x += 64) ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
  // the window itself
  ctx.strokeStyle = "rgba(158,203,255,0.35)";
  ctx.lineWidth = 2;
  ctx.setLineDash([14, 10]);
  ctx.strokeRect(5, 5, CW - 10, CH - 10);
  ctx.setLineDash([]);
  let x = CW - 44;
  x -=
    text(
      ctx,
      `${px(CW)} × ${px(CH)}`,
      x,
      52,
      `500 26px ${MONO}`,
      "rgba(158,203,255,0.55)",
      "right",
    ) + 18;
  x -=
    text(ctx, "Settings", x, 52, `600 30px ${FONT}`, "#eaf3ff", "right") + 14;
  text(
    ctx,
    "Window",
    x,
    52,
    `500 26px ${MONO}`,
    "rgba(158,203,255,0.7)",
    "right",
  );
  for (const c of CONTROLS) {
    const [cx, cy, cw, ch] = c.r;
    if (c.secret) {
      ctx.fillStyle = "#020306";
      rr(ctx, cx, cy, cw, ch, 10);
      ctx.fill();
      ctx.strokeStyle = "rgba(255,200,97,0.75)";
      ctx.lineWidth = 3;
      ctx.stroke();
      // the bar: nothing of it leaves the PC
      ctx.fillStyle = "rgba(255,200,97,0.12)";
      for (let hx = cx + 14; hx < cx + cw - 14; hx += 28)
        ctx.fillRect(hx, cy + 10, 12, ch - 20);
      const w1 = text(
        ctx,
        c.role,
        cx + 22,
        cy + ch / 2,
        `500 26px ${MONO}`,
        "rgba(255,200,97,0.85)",
      );
      text(
        ctx,
        "blacked out before anything is sent",
        cx + 22 + w1 + 22,
        cy + ch / 2,
        `600 27px ${FONT}`,
        "#ffd98c",
      );
      continue;
    }
    ctx.fillStyle = "rgba(158,203,255,0.07)";
    rr(ctx, cx, cy, cw, ch, 8);
    ctx.fill();
    ctx.strokeStyle = c.target
      ? "rgba(158,203,255,0.95)"
      : "rgba(158,203,255,0.6)";
    ctx.lineWidth = 2.5;
    ctx.stroke();
    // corner ticks, like a measuring tool
    ctx.strokeStyle = "#eaf3ff";
    ctx.lineWidth = 4;
    const t = 16;
    [
      [cx, cy, 1, 1],
      [cx + cw, cy, -1, 1],
      [cx, cy + ch, 1, -1],
      [cx + cw, cy + ch, -1, -1],
    ].forEach(([ax, ay, sx, sy]) => {
      ctx.beginPath();
      ctx.moveTo(ax, ay + sy * t);
      ctx.lineTo(ax, ay);
      ctx.lineTo(ax + sx * t, ay);
      ctx.stroke();
    });
    let lx = cx + 22;
    lx +=
      text(
        ctx,
        c.role,
        lx,
        cy + ch / 2,
        `500 28px ${MONO}`,
        "rgba(158,203,255,0.75)",
      ) + 20;
    lx +=
      text(ctx, c.name, lx, cy + ch / 2, `600 34px ${FONT}`, "#eaf3ff") + 20;
    if (c.state)
      text(ctx, c.state, lx, cy + ch / 2, `500 27px ${MONO}`, "#ffd98c");
    if (cw > 600)
      text(
        ctx,
        `${px(cx)}, ${px(cy)}  ${px(cw)} × ${px(ch)}`,
        cx + cw - 22,
        cy + ch / 2,
        `500 24px ${MONO}`,
        "rgba(158,203,255,0.5)",
        "right",
      );
  }
}

function layer(draw) {
  const c = document.createElement("canvas");
  c.width = CW;
  c.height = CH;
  draw(c.getContext("2d"));
  return c;
}

// ------------------------------------------------------------------ the lens
export function startLens(stage, track, { reduced = false, fly = null } = {}) {
  const world = stage.querySelector(".lens-world");
  const canvas = stage.querySelector("canvas.lens");
  const read = stage.querySelector(".lens-read");
  const exportEl = stage.querySelector(".lens-pt");
  if (!world || !canvas || !canvas.getContext) return false;
  const ctx = canvas.getContext("2d");
  if (!ctx || typeof ctx.roundRect !== "function") return false;
  let screen = layer(drawUI);
  let reads = layer(drawReads);
  document.fonts?.ready.then(() => {
    screen = layer(drawUI);
    reads = layer(drawReads);
    dirty = true;
  });

  // Sizing: the canvas fills the window at device resolution; drawing happens in canvas units.
  let cssW = 0;
  let scale = 1;
  let dirty = true;
  const fit = () => {
    const r = canvas.getBoundingClientRect();
    if (!r.width) return;
    const dpr = Math.min(2, devicePixelRatio || 1);
    cssW = r.width;
    scale = (r.width / CW) * dpr;
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(r.height * dpr);
    dirty = true;
  };
  new ResizeObserver(fit).observe(stage);
  fit();

  const centre = (c) => ({ x: c.r[0] + c.r[2] / 2, y: c.r[1] + c.r[3] / 2 });
  const lens = { ...centre(CONTROLS[6]), vx: 0, vy: 0, r: 0, riding: false };
  stage.lens = lens; // readable from the console
  const baseR = () => Math.min(CW, CH) * 0.3;
  let roamAt = 0;
  let roamUntil = 0;
  let pointedAt = 0;
  let visible = false;
  new IntersectionObserver(([e]) => (visible = e.isIntersecting)).observe(
    stage,
  );

  // The tilt follows the mouse a little, so the window reads as a pane of glass in space.
  if (!reduced && matchMedia("(hover: hover) and (pointer: fine)").matches) {
    stage.addEventListener("pointermove", (e) => {
      const r = stage.getBoundingClientRect();
      const nx = (e.clientX - r.left) / r.width - 0.5;
      const ny = (e.clientY - r.top) / r.height - 0.5;
      world.style.setProperty("--ry", `${(nx * 7).toFixed(2)}deg`);
      world.style.setProperty("--rx", `${(-ny * 5).toFixed(2)}deg`);
    });
    stage.addEventListener("pointerleave", () => {
      world.style.setProperty("--ry", "0deg");
      world.style.setProperty("--rx", "0deg");
    });
  }

  const under = (x, y) =>
    CONTROLS.find(
      (c) =>
        x >= c.r[0] &&
        x <= c.r[0] + c.r[2] &&
        y >= c.r[1] &&
        y <= c.r[1] + c.r[3],
    );
  let lastRead = "";
  const showRead = (x, y, on) => {
    const c = under(x, y);
    const key = c ? c.name : "window";
    if (key !== lastRead) {
      lastRead = key;
      read.innerHTML = c
        ? c.secret
          ? `<b>${c.role}</b> ${c.name} <em class="warn">blacked out</em>`
          : `<b>${c.role}</b> ${c.name}${c.state ? ` <em>${c.state}</em>` : ""} <i>${px(c.r[0])}, ${px(c.r[1])} · ${px(c.r[2])} × ${px(c.r[3])}</i>`
        : `<b>Window</b> Settings <i>${px(CW)} × ${px(CH)}</i>`;
    }
    read.classList.toggle("on", on);
    if (!on) return;
    const k = cssW / CW;
    const rr0 = Math.min(lens.r, baseR()) * k;
    const left = Math.min(
      cssW - read.offsetWidth - 10,
      Math.max(10, x * k + rr0 * 0.7),
    );
    const top = Math.min(
      cssW * (CH / CW) - read.offsetHeight - 10,
      Math.max(10, y * k + rr0 * 0.7),
    );
    read.style.transform = `translate3d(${left.toFixed(0)}px, ${top.toFixed(0)}px, 0)`;
  };

  const draw = (time) => {
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.clearRect(0, 0, CW, CH);
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(screen, 0, 0);
    if (lens.r < 2) return;
    ctx.save();
    ctx.beginPath();
    ctx.arc(lens.x, lens.y, lens.r, 0, Math.PI * 2);
    ctx.clip();
    ctx.drawImage(reads, 0, 0);
    ctx.restore();
    if (lens.r > CW) return; // the whole window is glass: no rim to draw
    // the rim: a ring of ice with a soft glow, and a dial of ticks turning slowly
    ctx.save();
    ctx.shadowColor = "rgba(158,203,255,0.9)";
    ctx.shadowBlur = 34;
    ctx.strokeStyle = "rgba(190,220,255,0.95)";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.arc(lens.x, lens.y, lens.r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
    ctx.strokeStyle = "rgba(158,203,255,0.22)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(lens.x, lens.y, lens.r + 14, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeStyle = "rgba(234,243,255,0.7)";
    ctx.lineWidth = 3;
    const rot = reduced ? 0 : time / 9000;
    for (let i = 0; i < 36; i++) {
      const a = rot + (i / 36) * Math.PI * 2;
      const len = i % 9 === 0 ? 16 : 7;
      ctx.beginPath();
      ctx.moveTo(
        lens.x + Math.cos(a) * (lens.r + 6),
        lens.y + Math.sin(a) * (lens.r + 6),
      );
      ctx.lineTo(
        lens.x + Math.cos(a) * (lens.r + 6 + len),
        lens.y + Math.sin(a) * (lens.r + 6 + len),
      );
      ctx.stroke();
    }
  };

  if (reduced) {
    // one still frame: the lens over Export, with its readout
    lens.r = baseR();
    const paint = () => {
      draw(0);
      showRead(lens.x, lens.y, true);
    };
    paint();
    document.fonts?.ready.then(paint);
    new ResizeObserver(paint).observe(stage);
    return true;
  }

  const tick = (time) => {
    requestAnimationFrame(tick);
    if (!visible || document.hidden) return;
    const step = track.dataset.step || "0";
    const hold = track.dataset.hold || "";
    const rect = canvas.getBoundingClientRect();
    const k = CW / rect.width;
    // Your firefly over the window carries the lens. Otherwise it roams on its own.
    const c = fly?.cursor;
    const riding =
      !!c &&
      c.x >= rect.left &&
      c.x <= rect.right &&
      c.y >= rect.top &&
      c.y <= rect.bottom;
    lens.riding = riding;
    let gx;
    let gy;
    if (riding) {
      gx = (fly.pos.x - rect.left) * k;
      gy = (fly.pos.y - rect.top) * k;
    } else {
      if (time > roamUntil) {
        roamAt = (roamAt + 1) % ROAM.length;
        roamUntil = time + 1900;
      }
      ({ x: gx, y: gy } = centre(CONTROLS[ROAM[roamAt]]));
    }
    const stiff = riding ? 0.5 : 0.09;
    lens.vx = (lens.vx + (gx - lens.x) * stiff) * (riding ? 0.5 : 0.72);
    lens.vy = (lens.vy + (gy - lens.y) * stiff) * (riding ? 0.5 : 0.72);
    lens.x += lens.vx;
    lens.y += lens.vy;
    const targetR =
      hold === "2"
        ? CW * 1.2
        : hold === "1" || hold === "3"
          ? 0
          : riding || step === "2" || step === "0"
            ? baseR()
            : 0;
    lens.r += (targetR - lens.r) * (targetR > lens.r ? 0.1 : 0.14);
    if (lens.r < 1) lens.r = 0;
    // Layer 3: while it's the firefly's turn, it threads light to Export and rings it.
    if ((step === "3" || hold === "3") && exportEl && fly && time > pointedAt) {
      pointedAt = time + 4200;
      fly.point(exportEl, exportEl.dataset.label, { hold: 3300 });
    }
    const moving =
      Math.abs(lens.vx) + Math.abs(lens.vy) > 0.05 ||
      Math.abs(targetR - lens.r) > 0.5 ||
      lens.r > 0;
    if (moving || dirty) {
      dirty = false;
      draw(time);
    }
    showRead(lens.x, lens.y, lens.r > 40 && lens.r < CW);
  };
  requestAnimationFrame(tick);
  return true;
}
