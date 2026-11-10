/**
 * The hero's stage in space: the pane of glass tilts toward your mouse (a few degrees, on a
 * spring), and gold dust drifts behind it with a little parallax, so the screenshots read as an
 * object on your desk rather than a picture on a page. Nothing here runs with reduced motion,
 * on a phone, or while the hero is scrolled away.
 */
export function initHero3D({ reduced = false } = {}) {
  const hero = document.querySelector(".hero");
  const stage = hero?.querySelector(".stage");
  const dust = stage?.querySelector(".hero-dust");
  if (!hero || !stage || reduced) return;
  const fine = matchMedia("(hover: hover) and (pointer: fine)").matches;
  if (!fine) return;

  // ---- the tilt
  let tx = 0;
  let ty = 0; // target, in -1..1
  let mx = 0;
  let my = 0; // smoothed
  hero.addEventListener("pointermove", (e) => {
    const r = hero.getBoundingClientRect();
    tx = ((e.clientX - r.left) / r.width - 0.5) * 2;
    ty = ((e.clientY - r.top) / r.height - 0.5) * 2;
  });
  hero.addEventListener("pointerleave", () => {
    tx = 0;
    ty = 0;
  });

  // ---- the dust
  const ctx = dust?.getContext("2d");
  const motes = Array.from({ length: 70 }, () => ({
    x: Math.random(),
    y: Math.random(),
    z: 0.3 + Math.random() * 0.7, // depth: far motes are small, slow and dim
    r: Math.random(),
    g: Math.random() < 0.8, // gold or ice
    p: Math.random() * Math.PI * 2,
  }));
  let w = 0;
  let h = 0;
  const fit = () => {
    if (!dust) return;
    const r = dust.getBoundingClientRect();
    const dpr = Math.min(2, devicePixelRatio || 1);
    w = r.width;
    h = r.height;
    dust.width = Math.round(w * dpr);
    dust.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  if (dust) {
    new ResizeObserver(fit).observe(dust);
    fit();
  }

  let visible = true;
  new IntersectionObserver(([e]) => (visible = e.isIntersecting), {
    threshold: 0.05,
  }).observe(hero);
  let last = 0;
  let lastTilt = "";
  const tick = (time) => {
    requestAnimationFrame(tick);
    if (!visible || document.hidden) return;
    const dt = Math.min(50, time - last) / 16.7;
    last = time;
    mx += (tx - mx) * 0.08 * dt;
    my += (ty - my) * 0.08 * dt;
    // only touch the page when the tilt really changed: a still mouse costs nothing
    const tilt = `${(mx * 10).toFixed(2)},${(-my * 7).toFixed(2)}`;
    if (tilt !== lastTilt) {
      lastTilt = tilt;
      stage.style.setProperty("--ry", `${(mx * 10).toFixed(2)}deg`);
      stage.style.setProperty("--rx", `${(-my * 7).toFixed(2)}deg`);
    }
    if (!ctx || !w) return;
    ctx.clearRect(0, 0, w, h);
    for (const m of motes) {
      m.p += 0.004 * dt * m.z;
      m.y -= 0.00022 * dt * m.z; // a slow rise
      if (m.y < -0.05) {
        m.y = 1.05;
        m.x = Math.random();
      }
      const px = (m.x + Math.sin(m.p) * 0.012 + mx * 0.025 * m.z) * w;
      const py = (m.y + my * 0.02 * m.z) * h;
      const tw = 0.55 + 0.45 * Math.sin(m.p * 3 + m.r * 9);
      const a = (0.12 + 0.5 * m.z) * tw;
      const rad = 0.6 + 2.2 * m.z * (0.6 + 0.4 * m.r);
      ctx.fillStyle = m.g
        ? `rgba(255, 210, 120, ${a.toFixed(3)})`
        : `rgba(158, 203, 255, ${(a * 0.8).toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(px, py, rad, 0, Math.PI * 2);
      ctx.fill();
      if (m.z > 0.85) {
        // the nearest motes glow a little
        ctx.fillStyle = m.g
          ? `rgba(255, 200, 97, ${(a * 0.18).toFixed(3)})`
          : `rgba(158, 203, 255, ${(a * 0.15).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(px, py, rad * 3.2, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  };
  requestAnimationFrame(tick);
}
