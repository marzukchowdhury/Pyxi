/**
 * The wall of screens: Pyxi's real screenshots stand on a ring in CSS 3D. Drag it, use the arrows
 * or the keyboard, or click a screen to bring it round; the front screen is lit and named below,
 * and its link takes you to the part of the page that explains it. On its own it turns slowly.
 * Reduced motion: it holds still until you turn it.
 */
import "./features.css";

if (document.body.classList.contains("page-features")) {
  const forceMotion = new URLSearchParams(location.search).has("motion");
  const reduced =
    !forceMotion && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wall = document.getElementById("wall");
  const ring = wall?.querySelector(".wall-ring");
  const screens = ring ? [...ring.querySelectorAll(".screen")] : [];
  const title = document.getElementById("wall-title");
  const line = document.getElementById("wall-line");
  if (ring && screens.length) {
    const n = screens.length;
    const step = 360 / n;
    let rot = 0; // where the ring is
    let goal = 0; // where it's going
    let idle = !reduced; // turning on its own
    let front = -1;
    let dragging = null;
    let visible = true;
    const radius = () => {
      const w = wall.clientWidth;
      // the screens are 38% of the wall wide; the ring is wide enough that they don't touch
      return (w * 0.38 * 1.18) / (2 * Math.tan(Math.PI / n));
    };
    const place = () => {
      const r = radius();
      ring.style.setProperty("--r", `${r.toFixed(0)}px`);
      screens.forEach((s, i) =>
        s.style.setProperty("--a", `${(i * step).toFixed(2)}deg`),
      );
    };
    const norm = (a) => ((a % 360) + 360) % 360;
    const show = (i) => {
      if (i === front) return;
      front = i;
      screens.forEach((s, k) => s.classList.toggle("front", k === i));
      const s = screens[i];
      if (title) title.textContent = s.dataset.title;
      if (line) line.textContent = s.dataset.line;
      if (window.pyxi?.say && !reduced) window.pyxi.say(s.dataset.title + ".");
    };
    const turnTo = (i) => {
      // the shortest way round to put screen i at the front
      const want = -i * step;
      const diff = ((want - goal + 540) % 360) - 180;
      goal += diff;
      idle = false;
    };
    const tick = (time) => {
      requestAnimationFrame(tick);
      if (!visible || document.hidden) return;
      if (idle && !dragging) goal -= 0.025;
      rot += (goal - rot) * (dragging ? 0.6 : 0.09);
      ring.style.setProperty("--rot", `${rot.toFixed(2)}deg`);
      // light and depth for each screen, from how far it is from the front
      let best = 0;
      let bestD = 999;
      screens.forEach((s, i) => {
        const d = Math.abs(((norm(i * step + rot) + 180) % 360) - 180); // 0 at the front, 180 at the back
        s.style.setProperty("--d", (d / 180).toFixed(3));
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      });
      show(best);
    };
    place();
    addEventListener("resize", place);
    new IntersectionObserver(([e]) => (visible = e.isIntersecting), {
      threshold: 0.1,
    }).observe(wall);
    requestAnimationFrame(tick);

    // turn it: arrows, keys, drag, click a side screen
    document.querySelectorAll("[data-turn]").forEach((b) =>
      b.addEventListener("click", () => {
        goal -= Number(b.dataset.turn) * step;
        idle = false;
      }),
    );
    wall.tabIndex = 0;
    wall.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") goal -= step;
      else if (e.key === "ArrowLeft") goal += step;
      else return;
      idle = false;
      e.preventDefault();
    });
    wall.addEventListener("pointerdown", (e) => {
      dragging = { x: e.clientX, start: goal, moved: false };
      wall.setPointerCapture(e.pointerId);
    });
    wall.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const dx = e.clientX - dragging.x;
      if (Math.abs(dx) > 4) dragging.moved = true;
      goal = dragging.start + dx * 0.25;
    });
    const drop = () => {
      if (!dragging) return;
      // settle on the nearest screen
      goal = Math.round(goal / step) * step;
      idle = false;
      const moved = dragging.moved;
      dragging = null;
      if (moved) wall.classList.add("dragged");
      setTimeout(() => wall.classList.remove("dragged"), 50);
    };
    wall.addEventListener("pointerup", drop);
    wall.addEventListener("pointercancel", drop);
    screens.forEach((s, i) =>
      s.addEventListener("click", (e) => {
        if (wall.classList.contains("dragged")) {
          e.preventDefault();
          return;
        }
        if (i !== front) {
          e.preventDefault();
          turnTo(i);
        }
      }),
    );
    // the pane tilts a little toward the mouse
    if (!reduced && matchMedia("(hover: hover) and (pointer: fine)").matches) {
      wall.addEventListener("pointermove", (e) => {
        if (dragging) return;
        const r = wall.getBoundingClientRect();
        wall.style.setProperty(
          "--tx",
          `${(((e.clientY - r.top) / r.height - 0.5) * -6).toFixed(2)}deg`,
        );
      });
      wall.addEventListener("pointerleave", () =>
        wall.style.setProperty("--tx", "0deg"),
      );
    }
  }
}
