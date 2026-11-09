/**
 * Why Pyxi: "the same task, two ways". Two clipped panes each hold a real CSS 3D world of the
 * same document; a divider you drag (pointer, touch, arrow keys) decides how much of each shows.
 * Left of it, the chat-box way; right of it, the Pyxi way. On its own the divider sweeps slowly
 * back and forth until you grab it. The whole scene tilts a little with the mouse, and the ring,
 * thread and firefly on the Pyxi side are placed on the actual typo, wherever the text wraps.
 */
import "./why.css";

if (document.body.classList.contains("page-why")) init();

function init() {
  const forceMotion = new URLSearchParams(location.search).has("motion");
  const reduced =
    !forceMotion && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const stage = document.getElementById("twoways");
  if (!stage) return;
  const handle = stage.querySelector(".tw-handle");
  const right = stage.querySelector(".tw-right");
  const fly = window.pyxi?.fly;

  // ------------------------------------------------------------ the divider
  let split = 50;
  let grabbed = reduced; // with reduced motion there's no auto sweep: the divider waits for you
  let pointedAt = false;
  const set = (v) => {
    split = Math.max(3, Math.min(97, v));
    stage.style.setProperty("--split", split.toFixed(2));
    const n = Math.round(split);
    handle.setAttribute("aria-valuenow", String(n));
    handle.setAttribute(
      "aria-valuetext",
      n < 35
        ? "Mostly the Pyxi way"
        : n > 65
          ? "Mostly the chat-box way"
          : "Half and half",
    );
    // The first time you reveal most of Pyxi's side, the page's firefly joins in and points at the typo.
    if (split < 40 && fly && !pointedAt) {
      pointedAt = true;
      const ring = right.querySelector(".tw-ring");
      fly.point(ring, "The typo", { hold: 3200 });
    }
  };
  const fromX = (clientX) => {
    const r = stage.getBoundingClientRect();
    set(((clientX - r.left) / r.width) * 100);
  };
  const grab = () => {
    grabbed = true;
    stage.classList.add("grabbing");
  };
  let dragging = false;
  stage.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    dragging = true;
    grab();
    stage.setPointerCapture?.(e.pointerId);
    fromX(e.clientX);
  });
  stage.addEventListener("pointermove", (e) => {
    if (dragging) fromX(e.clientX);
  });
  const drop = () => {
    dragging = false;
    stage.classList.remove("grabbing");
  };
  stage.addEventListener("pointerup", drop);
  stage.addEventListener("pointercancel", drop);
  handle.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 10 : 3;
    const moves = {
      ArrowLeft: -step,
      ArrowRight: step,
      ArrowDown: -step,
      ArrowUp: step,
      Home: -100,
      End: 100,
    };
    if (!(e.key in moves)) return;
    e.preventDefault();
    grabbed = true;
    set(split + moves[e.key]);
  });

  // ------------------------------------------------------------ the tilt
  if (!reduced && matchMedia("(hover: hover) and (pointer: fine)").matches) {
    stage.addEventListener("pointermove", (e) => {
      const r = stage.getBoundingClientRect();
      const nx = (e.clientX - r.left) / r.width - 0.5;
      const ny = (e.clientY - r.top) / r.height - 0.5;
      stage.style.setProperty("--ry", `${(nx * 7).toFixed(2)}deg`);
      stage.style.setProperty("--rx", `${(-ny * 5).toFixed(2)}deg`);
    });
    stage.addEventListener("pointerleave", () => {
      stage.style.setProperty("--ry", "0deg");
      stage.style.setProperty("--rx", "0deg");
    });
  }

  // ------------------------------------------------------------ the typo: ring, thread, firefly, cursor
  const place = () => {
    const doc = right.querySelector(".tw-doc");
    const mark = doc.querySelector("mark.typo");
    const ring = doc.querySelector(".tw-ring");
    const flyDot = doc.querySelector(".tw-fly");
    const cursor = doc.querySelector(".tw-cursor");
    const thread = doc.querySelector(".tw-thread");
    if (!mark || !ring) return;
    // offsetLeft/Top are relative to the document plane, whatever the 3D transform above it.
    const x = mark.offsetLeft;
    const y = mark.offsetTop;
    const w = mark.offsetWidth;
    const h = mark.offsetHeight;
    const u = doc.offsetWidth / 100; // one percent of the document's width
    ring.style.left = `${x - 0.8 * u}px`;
    ring.style.top = `${y - 0.5 * u}px`;
    ring.style.width = `${w + 1.6 * u}px`;
    ring.style.height = `${h + 1 * u}px`;
    const cx = x + w + 3.2 * u;
    const cy = y + h + 2.6 * u;
    cursor.style.left = `${cx}px`;
    cursor.style.top = `${cy}px`;
    const fx = cx + 2.6 * u;
    const fy = cy + 2.2 * u;
    flyDot.style.left = `${fx}px`;
    flyDot.style.top = `${fy}px`;
    const tx = x + w + 0.8 * u;
    const ty = y + h / 2;
    const mx = (fx + tx) / 2 + 2 * u;
    const my = (fy + ty) / 2 + 1.5 * u;
    thread.setAttribute(
      "viewBox",
      `0 0 ${doc.offsetWidth} ${doc.offsetHeight}`,
    );
    thread
      .querySelector("path")
      .setAttribute(
        "d",
        `M ${fx + 0.5 * u} ${fy + 0.5 * u} Q ${mx} ${my} ${tx} ${ty}`,
      );
  };
  // On the chat side, the selection highlight sits on the paragraph you'd be copying.
  const placeSelect = () => {
    const doc = stage.querySelector(".tw-left .tw-doc");
    const para = doc?.querySelector(".tw-para");
    const sel = doc?.querySelector(".tw-select");
    if (!para || !sel) return;
    const u = doc.offsetWidth / 100;
    sel.style.left = `${para.offsetLeft - 0.8 * u}px`;
    sel.style.top = `${para.offsetTop - 0.4 * u}px`;
    sel.style.width = `${para.offsetWidth + 1.6 * u}px`;
    sel.style.height = `${para.offsetHeight + 0.8 * u}px`;
  };
  const placeAll = () => {
    place();
    placeSelect();
  };
  placeAll();
  new ResizeObserver(placeAll).observe(stage);
  document.fonts?.ready.then(placeAll);

  // ------------------------------------------------------------ the sweep
  let visible = false;
  new IntersectionObserver(([e]) => (visible = e.isIntersecting), {
    threshold: 0.3,
  }).observe(stage);
  const t0 = performance.now();
  const sweep = (t) => {
    if (grabbed) return;
    requestAnimationFrame(sweep);
    if (!visible || document.hidden) return;
    set(50 + 32 * Math.sin(((t - t0) / 5200) * Math.PI * 2));
  };
  if (!grabbed) requestAnimationFrame(sweep);
  set(split);
}
