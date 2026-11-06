/**
 * Pyxi's presence on the page, ported from the app's Presence.tsx: a little firefly that rides
 * beside your cursor on a stiff, well-damped spring, hovers in a lazy figure of eight when you're
 * still, and leaves a dotted trail of light when you move.
 *
 * It lives on the dot of the "i" in the headline until you move the mouse, and goes home there
 * when the cursor leaves the window. With no cursor to ride (a phone, or before you've moved
 * the mouse) it flies over to whatever it's pointing at, then back.
 *
 * point(el, label) does what Pyxi does on your desktop: a thread of light from the firefly to the
 * target, an expanding ice ring on it and a small tag, recomputed every frame so it stays
 * attached while you scroll.
 */
const SVGNS = "http://www.w3.org/2000/svg";
const TRAIL = 12;
const OFFSET = { x: 20, y: 18 }; // where the firefly sits relative to the cursor
const CORE_PX = 9.25; // the glowing tail's height at scale 1, to match the size of the i's dot

export class Firefly {
  constructor({ reduced = false } = {}) {
    this.root = document.querySelector(".presence");
    this.tether = document.querySelector(".tether");
    if (!this.root || !this.tether) return;
    this.reduced = reduced;
    this.thread = this.tether.querySelector(".thread-line");
    this.spark = this.tether.querySelector(".thread-spark");
    this.target = this.tether.querySelector(".target");
    this.tag = document.querySelector(".target-tag");
    this.lantern = document.querySelector(".lantern");
    this.perch = document.querySelector("[data-perch]");
    this.fine = matchMedia("(hover: hover) and (pointer: fine)").matches;
    this.cursor = null; // last pointer position, null when there's no cursor on the page
    this.pointing = null;
    this.history = [];
    const start = this.home() ?? { x: innerWidth - 80, y: innerHeight - 120 };
    this.pos = { ...start, vx: 0, vy: 0, s: 1 };

    const g = this.tether.querySelector(".ff-trail");
    this.trail = Array.from({ length: TRAIL - 1 }, () => {
      const c = document.createElementNS(SVGNS, "circle");
      c.setAttribute("r", "1");
      c.style.opacity = "0";
      g.append(c);
      return c;
    });

    if (this.fine) {
      addEventListener("pointermove", (e) => {
        if (e.pointerType === "mouse") this.cursor = { x: e.clientX, y: e.clientY };
      }, { passive: true });
      document.documentElement.addEventListener("pointerleave", () => (this.cursor = null));
      addEventListener("blur", () => (this.cursor = null));
    }
    this.tick = this.tick.bind(this);
    requestAnimationFrame(this.tick);
  }

  /** The dot of the i, in viewport coordinates, and how big the firefly should be to fill it. */
  home() {
    if (!this.perch) return null;
    const r = this.perch.getBoundingClientRect();
    if (r.bottom < -40 || r.top > innerHeight + 40) return null;
    // offsetHeight, not the rect: the dot shrinks away while the firefly sits on it.
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, s: Math.max(1, this.perch.offsetHeight / CORE_PX) };
  }

  /** Thread light from the firefly to el, ring it, and tag it with label. */
  point(el, label = "", { hold = 3400 } = {}) {
    if (!el || !this.root) return;
    const now = performance.now();
    this.pointing = { el, label, start: now, until: now + hold };
    this.tag.textContent = label;
    this.tag.classList.toggle("on", !!label);
    this.target.classList.remove("on");
    void this.target.getBoundingClientRect(); // restart the ring animation
    this.target.classList.add("on");
  }

  tick(time) {
    const p = this.pos;
    const now = performance.now();
    const pt = this.pointing && now < this.pointing.until ? this.pointing : null;
    let tx = 0;
    let ty = 0;
    let onScreen = false;
    if (pt) {
      const r = pt.el.getBoundingClientRect();
      tx = r.left + r.width / 2;
      ty = r.top + r.height / 2;
      onScreen = ty > 40 && ty < innerHeight - 10 && tx > 0 && tx < innerWidth;
    }

    // Where to be: beside the cursor; or, with no cursor, near what it's pointing at; or home.
    const home = this.home();
    let goal;
    let scale = 1;
    if (this.cursor) goal = { x: this.cursor.x + OFFSET.x, y: this.cursor.y + OFFSET.y };
    else if (pt && onScreen) goal = this.near(tx, ty);
    else if (home) {
      goal = { x: home.x, y: home.y };
      scale = home.s;
    } else goal = this.fine ? { x: innerWidth - 70, y: innerHeight - 90 } : { x: innerWidth - 42, y: innerHeight - 64 };

    if (this.reduced) {
      p.x = goal.x;
      p.y = goal.y;
      p.vx = p.vy = 0;
    } else {
      // The app's spring: stiff and well damped, it keeps up and settles without wobble.
      p.vx = (p.vx + (goal.x - p.x) * 0.2) * 0.62;
      p.vy = (p.vy + (goal.y - p.y) * 0.2) * 0.62;
      p.x += p.vx;
      p.y += p.vy;
    }
    p.s += (scale - p.s) * 0.16;
    const atHome = !this.cursor && home && Math.hypot(home.x - p.x, home.y - p.y) < 3;
    document.body.classList.toggle("ff-home", !!atHome);
    // Hovering: a slow, lazy figure of eight, the way a firefly hangs in the air. Not while perched.
    const sway = atHome || this.reduced ? 0 : 1;
    const hx = p.x + Math.sin(time / 820) * 2.2 * sway;
    const hy = p.y + Math.sin(time / 1270) * 2.8 * sway;
    this.root.style.transform = `translate3d(${hx.toFixed(2)}px, ${hy.toFixed(2)}px, 0) scale(${p.s.toFixed(3)})`;
    this.root.classList.toggle("mode-acting", !!pt);
    this.root.classList.toggle("mode-idle", !pt);
    if (this.lantern) {
      this.lantern.style.setProperty("--fx", `${hx.toFixed(0)}px`);
      this.lantern.style.setProperty("--fy", `${hy.toFixed(0)}px`);
    }

    // The trail: where the tail has just been, fading out behind it. Still means no trail.
    const h = this.history;
    h.unshift({ x: hx, y: hy });
    if (h.length > TRAIL) h.length = TRAIL;
    const speed = Math.hypot(p.vx, p.vy);
    this.trail.forEach((dot, i) => {
      const at = h[Math.min(h.length - 1, i + 1)];
      const fade = 1 - i / TRAIL;
      dot.setAttribute("cx", at.x.toFixed(1));
      dot.setAttribute("cy", at.y.toFixed(1));
      dot.setAttribute("r", (0.6 + 2.2 * fade).toFixed(2));
      dot.style.opacity = this.reduced ? "0" : (Math.min(1, speed / 6) * fade * 0.7).toFixed(3);
    });

    // The thread: firefly to target, a gentle curve, alive only while the target is fresh.
    if (pt && onScreen) {
      const sx = hx;
      const sy = hy;
      const mx = (sx + tx) / 2 + (ty - sy) * 0.18;
      const my = (sy + ty) / 2 - (tx - sx) * 0.18;
      this.thread.setAttribute("d", `M ${sx.toFixed(1)} ${sy.toFixed(1)} Q ${mx.toFixed(1)} ${my.toFixed(1)} ${tx.toFixed(1)} ${ty.toFixed(1)}`);
      const age = (now - pt.start) / 1000;
      this.thread.style.opacity = String(Math.min(1, age * 4) * Math.min(1, (pt.until - now) / 500));
      if (age < 0.8 && !this.reduced) {
        const k = Math.min(1, age / 0.55);
        const e = 1 - Math.pow(1 - k, 3);
        const u = 1 - e;
        this.spark.setAttribute("cx", String(u * u * sx + 2 * u * e * mx + e * e * tx));
        this.spark.setAttribute("cy", String(u * u * sy + 2 * u * e * my + e * e * ty));
        this.spark.style.opacity = k < 1 ? "1" : "0";
      } else this.spark.style.opacity = "0";
      this.target.setAttribute("transform", `translate(${tx.toFixed(1)} ${ty.toFixed(1)})`);
      this.target.classList.add("on");
      // The tag sits beside the target, on whichever side has room.
      const w = this.tag.offsetWidth;
      const left = tx + 16 + w > innerWidth - 8 ? tx - 16 - w : tx + 16;
      this.tag.style.transform = `translate3d(${left.toFixed(0)}px, ${(ty + 12).toFixed(0)}px, 0)`;
      this.tag.style.opacity = label(pt) ? String(Math.min(1, (pt.until - now) / 400)) : "0";
    } else {
      this.thread.style.opacity = "0";
      this.spark.style.opacity = "0";
      this.target.classList.remove("on");
      this.tag.style.opacity = "0";
      if (this.pointing && now >= this.pointing.until) {
        this.pointing = null;
        this.tag.classList.remove("on");
      }
    }
    requestAnimationFrame(this.tick);
  }

  /** A spot just up and to the left of a target, kept on screen. */
  near(tx, ty) {
    const x = tx < 90 ? tx + 56 : tx - 52;
    const y = ty < 140 ? ty + 54 : ty - 44;
    return { x: Math.min(innerWidth - 24, Math.max(24, x)), y: Math.min(innerHeight - 24, Math.max(90, y)) };
  }
}

const label = (pt) => !!pt.label;
