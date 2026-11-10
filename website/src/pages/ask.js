/**
 * Ask anything: a galaxy of the sixty real asks. Each one floats as a glowing label in 3D,
 * clustered by kind, turning slowly. Type a word and the matches fly forward while the rest
 * recede; hover one and it comes closer while the page's firefly points at who does it; pick
 * a kind and that cluster comes to the front. Drag to turn it. The labels are read from the
 * palette further down the page, so there is one list.
 */
import "./ask.css";

if (document.body.classList.contains("page-ask")) start();

function start() {
  const forceMotion =
    new URLSearchParams(location.search).has("motion") ||
    document.documentElement.classList.contains("force-motion");
  const reduced =
    !forceMotion && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const canvas = document.getElementById("galaxy");
  const wrap = canvas?.parentElement;
  const input = document.getElementById("gal-input");
  const count = document.getElementById("gal-count");
  const chips = [...document.querySelectorAll("#gal-chips button")];
  const pt = wrap?.querySelector(".gal-pt");
  const items = [...document.querySelectorAll("#asks-grid li")];
  if (!canvas || !items.length) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  // ---------------------------------------------------------------- the stars
  // One cluster per kind, spread evenly over a sphere; each ask sits a little way off its centre.
  const cats = [...new Set(items.map((li) => li.dataset.cat))];
  const centres = cats.map((_, i) => {
    const k = (i + 0.5) / cats.length;
    const y = 1 - 2 * k;
    const r = Math.sqrt(1 - y * y);
    const a = i * 2.399963; // the golden angle
    return { x: Math.cos(a) * r, y: y * 0.8, z: Math.sin(a) * r };
  });
  const seeded = (n) => {
    const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
    return s - Math.floor(s);
  };
  const stars = items.map((li, i) => {
    const c = centres[cats.indexOf(li.dataset.cat)];
    const u = seeded(i) * Math.PI * 2;
    const v = Math.acos(2 * seeded(i + 60) - 1);
    const d = 0.16 + seeded(i + 120) * 0.26;
    return {
      li,
      cat: li.dataset.cat,
      who: li.dataset.who,
      color: li.style.getPropertyValue("--c").trim() || "#ffc861",
      text: li.querySelector("q").textContent,
      bn: li.getAttribute("lang") === "bn",
      home: {
        x: c.x + Math.sin(v) * Math.cos(u) * d,
        y: c.y + Math.cos(v) * d,
        z: c.z + Math.sin(v) * Math.sin(u) * d,
      },
      pull: 0, // towards the camera (search match, hover, chosen kind)
      fade: 0, // away and dim (everything else while you're choosing)
      key: (li.textContent + " " + li.dataset.who)
        .toLowerCase()
        .normalize("NFKD"),
      box: null, // where its label was drawn last frame, for hit tests
    };
  });

  // ---------------------------------------------------------------- state
  let query = "";
  let cat = "";
  let hovered = null;
  let w = 0;
  let h = 0;
  let dpr = 1;
  let rotY = 0.6;
  let rotX = -0.12;
  let spin = 0; // extra turn from dragging, eased
  let mouse = { x: 0, y: 0 }; // -1..1 over the canvas
  let dragging = null;
  let visible = true;
  let lastPointed = 0;
  const fly = () => window.pyxi?.fly;

  const fit = () => {
    const r = wrap.getBoundingClientRect();
    if (!r.width) return;
    dpr = Math.min(2, devicePixelRatio || 1);
    w = r.width;
    h = r.height;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    if (reduced) render(0);
  };
  new ResizeObserver(fit).observe(wrap);
  fit();
  new IntersectionObserver(([e]) => (visible = e.isIntersecting)).observe(wrap);

  const norm = (s) => s.toLowerCase().normalize("NFKD");
  const applyFilter = () => {
    query = norm(input.value.trim());
    let n = 0;
    for (const s of stars)
      if ((!cat || s.cat === cat) && (!query || s.key.includes(query))) n++;
    count.textContent = String(n);
    if (reduced) render(0);
  };
  input.addEventListener("input", applyFilter);
  chips.forEach((b) =>
    b.addEventListener("click", () => {
      cat = b.dataset.cat;
      chips.forEach((c) => c.classList.toggle("on", c === b));
      applyFilter();
    }),
  );

  // ---------------------------------------------------------------- the mouse
  const toLocal = (e) => {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  const hit = (p) => {
    let best = null;
    for (let i = stars.length - 1; i >= 0; i--) {
      const b = stars[i].box;
      if (
        b &&
        p.x >= b.x &&
        p.x <= b.x + b.w &&
        p.y >= b.y &&
        p.y <= b.y + b.h
      ) {
        if (!best || b.z > best.box.z) best = stars[i];
      }
    }
    return best;
  };
  canvas.addEventListener("pointermove", (e) => {
    const p = toLocal(e);
    mouse = { x: (p.x / w) * 2 - 1, y: (p.y / h) * 2 - 1 };
    if (dragging) {
      spin += (e.clientX - dragging.x) * 0.006;
      rotX = Math.max(
        -0.6,
        Math.min(0.6, rotX + (e.clientY - dragging.y) * 0.004),
      );
      dragging = { x: e.clientX, y: e.clientY, moved: true };
      return;
    }
    const s = e.pointerType === "mouse" ? hit(p) : null;
    if (s !== hovered) {
      hovered = s;
      canvas.style.cursor = s ? "pointer" : "grab";
      if (s && pt && fly() && performance.now() - lastPointed > 350) {
        lastPointed = performance.now();
        placePt(s);
        fly().point(pt, s.who, { hold: 2200 });
      }
    }
    if (reduced) render(0);
  });
  canvas.addEventListener("pointerdown", (e) => {
    dragging = { x: e.clientX, y: e.clientY, moved: false };
    canvas.setPointerCapture(e.pointerId);
    canvas.style.cursor = "grabbing";
  });
  const stopDrag = (e) => {
    if (!dragging) return;
    const moved = dragging.moved;
    dragging = null;
    canvas.style.cursor = hovered ? "pointer" : "grab";
    if (moved) return;
    const s = hit(toLocal(e));
    if (s) open(s);
  };
  canvas.addEventListener("pointerup", stopDrag);
  canvas.addEventListener("pointercancel", () => (dragging = null));
  canvas.addEventListener("pointerleave", () => {
    hovered = null;
    mouse = { x: 0, y: 0 };
    if (reduced) render(0);
  });
  const placePt = (s) => {
    const b = s.box;
    if (!b) return;
    pt.style.transform = `translate(${(b.x + b.w / 2).toFixed(0)}px, ${(b.y + b.h / 2).toFixed(0)}px)`;
  };
  /** Clicking an ask takes you to its card in the list below and lights it up. */
  const open = (s) => {
    const pal = document.getElementById("pal-input");
    if (pal && (pal.value || s.li.classList.contains("hide"))) {
      pal.value = "";
      pal.dispatchEvent(new Event("input", { bubbles: true }));
    }
    s.li.scrollIntoView({
      behavior: reduced ? "auto" : "smooth",
      block: "center",
    });
    s.li.classList.add("lit");
    setTimeout(() => s.li.classList.remove("lit"), 2400);
    window.pyxi?.say?.(`${s.who} does that one.`);
  };

  // ---------------------------------------------------------------- drawing
  const sprites = new Map();
  const sprite = (color) => {
    let c = sprites.get(color);
    if (c) return c;
    c = document.createElement("canvas");
    c.width = c.height = 64;
    const g = c.getContext("2d");
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, "#ffffff");
    grad.addColorStop(0.18, color);
    grad.addColorStop(0.45, color + "55");
    grad.addColorStop(1, color + "00");
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    sprites.set(color, c);
    return c;
  };
  const dust = Array.from({ length: 170 }, (_, i) => {
    const u = seeded(i + 300) * Math.PI * 2;
    const v = Math.acos(2 * seeded(i + 500) - 1);
    const r = 1.5 + seeded(i + 700) * 0.9;
    return {
      x: Math.sin(v) * Math.cos(u) * r,
      y: Math.cos(v) * r * 0.7,
      z: Math.sin(v) * Math.sin(u) * r,
      a: 0.25 + seeded(i + 900) * 0.5,
    };
  });
  const font = (px, bn) =>
    `${bn ? 500 : 600} ${px.toFixed(1)}px ${bn ? '"Noto Sans Bengali", "Nirmala UI",' : ""} "Inter Variable", Inter, "Segoe UI", sans-serif`;
  const CAM = 2.9;

  function render(time) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const f = Math.min(w, h) * 1.32;
    const cx = w / 2;
    const cy = h / 2;
    const ry = rotY + spin + mouse.x * 0.18;
    const rx = rotX + mouse.y * 0.1;
    const cosY = Math.cos(ry);
    const sinY = Math.sin(ry);
    const cosX = Math.cos(rx);
    const sinX = Math.sin(rx);
    const anyFocus = !!query || !!cat;
    const pulse = 0.5 + 0.5 * Math.sin(time / 900);

    // dust far behind, so the asks hang in space
    ctx.fillStyle = "#9ecbff";
    for (const d of dust) {
      const x = d.x * cosY - d.z * sinY;
      let z = d.x * sinY + d.z * cosY;
      const y = d.y * cosX - z * sinX;
      z = d.y * sinX + z * cosX;
      const depth = CAM - z;
      if (depth < 0.6) continue;
      const k = f / depth;
      ctx.globalAlpha = d.a * Math.max(0.08, Math.min(0.5, (1.2 - z) / 4));
      ctx.beginPath();
      ctx.arc(
        cx + x * k,
        cy + y * k,
        Math.max(0.5, 1.6 / depth),
        0,
        Math.PI * 2,
      );
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    const drawn = [];
    for (const s of stars) {
      const match =
        (!cat || s.cat === cat) && (!query || s.key.includes(query));
      const wantPull =
        s === hovered ? 1 : anyFocus && match ? (query ? 0.78 : 0.55) : 0;
      const wantFade = anyFocus && !match ? 1 : 0;
      if (reduced) {
        s.pull = wantPull;
        s.fade = wantFade;
      } else {
        s.pull += (wantPull - s.pull) * 0.09;
        s.fade += (wantFade - s.fade) * 0.08;
      }
      // rotate home around Y then X
      const hx = s.home.x;
      const hy = s.home.y;
      const hz = s.home.z;
      let x = hx * cosY - hz * sinY;
      let z = hx * sinY + hz * cosY;
      let y = hy * cosX - z * sinX;
      z = hy * sinX + z * cosX;
      // pulled: towards the camera and the middle; faded: away and off to the side a little
      const p = s.pull;
      x = x * (1 - p * 0.55);
      y = y * (1 - p * 0.55) - p * 0.05;
      z = z + p * (1.45 - z * 0.5) - s.fade * 0.9;
      const depth = CAM - z;
      if (depth < 0.35) continue;
      const k = f / depth;
      const sx = cx + x * k;
      const sy = cy + y * k;
      const near = Math.max(0, Math.min(1, (z + 1.1) / 2.4)); // 0 far, 1 close
      drawn.push({ s, sx, sy, k, z, near, match });
    }
    drawn.sort((a, b) => a.z - b.z);

    // faint threads from each cluster's stars to its centre, so the kinds read as constellations
    ctx.lineWidth = 1;
    for (const d of drawn) {
      if (d.s.fade > 0.5 || d.near < 0.2) continue;
      const c = centres[cats.indexOf(d.s.cat)];
      let x = c.x * cosY - c.z * sinY;
      let z = c.x * sinY + c.z * cosY;
      const y = c.y * cosX - z * sinX;
      z = c.y * sinX + z * cosX;
      const k = f / (CAM - z);
      ctx.strokeStyle = d.s.color;
      ctx.globalAlpha = 0.06 + d.near * 0.08 * (1 - d.s.fade);
      ctx.beginPath();
      ctx.moveTo(d.sx, d.sy);
      ctx.lineTo(cx + x * k, cy + y * k);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // Which labels get their words: the nearest first, and never on top of one already placed.
    const placed = [];
    const wordy = new Set();
    const bySize = [...drawn].sort(
      (a, b) => b.s.pull + b.near - (a.s.pull + a.near),
    );
    for (const d of bySize) {
      const { s, sx, sy, near } = d;
      if (!(near > 0.44 || s.pull > 0.25) || s.fade > 0.85) continue;
      const px = (9.5 + near * 6.5 + s.pull * 5) * Math.min(1.25, w / 720);
      ctx.font = font(px, s.bn);
      const tw = ctx.measureText(s.text).width;
      const bw = tw + px * 1.4;
      const bh = px * 1.9;
      const bx = sx + 9 + near * 6;
      const by = sy - bh / 2;
      const clash = placed.some(
        (b) =>
          bx < b.x + b.w + 6 &&
          bx + bw + 6 > b.x &&
          by < b.y + b.h + 4 &&
          by + bh + 4 > b.y,
      );
      if (clash && s.pull < 0.5) continue;
      placed.push({ x: bx, y: by, w: bw, h: bh });
      wordy.add(s);
    }

    for (const d of drawn) {
      const { s, sx, sy, near } = d;
      const vis = (1 - s.fade * 0.82) * (0.25 + near * 0.75);
      // the glow
      const g = sprite(s.color);
      const gs = (14 + near * 34 + s.pull * 22) * (1 + pulse * 0.08);
      ctx.globalAlpha = vis * (0.55 + s.pull * 0.45);
      ctx.drawImage(g, sx - gs / 2, sy - gs / 2, gs, gs);
      // the label: far ones are just stars, nearer ones get their words
      if (!wordy.has(s)) {
        s.box = null;
        continue;
      }
      const px = (9.5 + near * 6.5 + s.pull * 5) * Math.min(1.25, w / 720);
      ctx.font = font(px, s.bn);
      const tw = ctx.measureText(s.text).width;
      const padX = px * 0.7;
      const bw = tw + padX * 2;
      const bh = px * 1.9;
      const bx = sx + 9 + near * 6;
      const by = sy - bh / 2;
      const textA =
        Math.max(0.25, (near - 0.44) / 0.56) * (1 - s.fade * 0.9) +
        s.pull * 0.6;
      ctx.globalAlpha = Math.min(1, textA);
      ctx.fillStyle =
        s.pull > 0.5 ? "rgba(14, 17, 25, 0.96)" : "rgba(10, 12, 18, 0.84)";
      ctx.beginPath();
      ctx.roundRect(bx, by, bw, bh, bh / 2);
      ctx.fill();
      ctx.strokeStyle = s.color;
      ctx.globalAlpha = Math.min(1, textA) * (0.28 + s.pull * 0.6);
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.globalAlpha = Math.min(1, textA);
      ctx.fillStyle = s.pull > 0.5 ? "#ffffff" : "#eef3fb";
      ctx.textBaseline = "middle";
      ctx.textAlign = "left";
      ctx.fillText(s.text, bx + padX, sy + px * 0.04);
      if (s.pull > 0.5) {
        // who does it, in its colour, under the label
        ctx.font = `650 ${(px * 0.62).toFixed(1)}px "Inter Variable", Inter, sans-serif`;
        ctx.fillStyle = s.color;
        ctx.globalAlpha = (s.pull - 0.5) * 2;
        ctx.fillText(s.who.toUpperCase(), bx + padX, by + bh + px * 0.55);
      }
      s.box = { x: bx, y: by, w: bw, h: bh, z: d.z };
    }
    ctx.globalAlpha = 1;
    if (hovered && pt) placePt(hovered);
  }

  if (reduced) {
    applyFilter();
    document.fonts?.ready.then(() => render(0));
    return;
  }
  let last = performance.now();
  const tick = (time) => {
    requestAnimationFrame(tick);
    if (!visible || document.hidden) {
      last = time;
      return;
    }
    const dt = Math.min(50, time - last);
    last = time;
    if (!dragging && !hovered) rotY += dt * 0.00007;
    spin *= 0.96;
    render(time);
  };
  requestAnimationFrame(tick);
  applyFilter();
}
