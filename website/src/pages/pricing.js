/**
 * The pricing page: three plans as jars of fireflies (more light in the bigger plans), a
 * monthly / yearly switch, and a "what you get" strip that fills for the plan under the pointer.
 * Every number is in the page's own markup (pricing.html); this file only moves them about.
 * Reduced motion: the jars are drawn once, still, and nothing counts or flies.
 */
import "./pricing.css";

if (document.body.classList.contains("page-pricing")) init();

function init() {
  const force = new URLSearchParams(location.search).has("motion") || document.documentElement.classList.contains("force-motion");
  const reduced = !force && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const plans = [...document.querySelectorAll(".plan")];
  const LIMITS = { starter: { q: 40, m: 10, t: 30, b: 1 }, pro: { q: 100, m: 50, t: 250, b: 3 }, max: { q: 150, m: 150, t: 800, b: 6 } };
  const TOP = LIMITS.max;
  const NAMES = { starter: "Starter", pro: "Pro", max: "Max" };

  // ---- monthly / yearly
  const bills = [...document.querySelectorAll("[data-bill]")];
  const setBill = (mode) => {
    bills.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.bill === mode)));
    for (const p of plans) {
      const d = p.dataset;
      p.querySelector(".amount").textContent = `$${mode === "year" ? d.permonth : d.month}`;
      p.querySelector(".per").textContent = mode === "year" ? "a month, billed yearly" : "a month";
      p.querySelector(".price-note").textContent = mode === "year" ? `$${d.year} a year. Two months free.` : `or $${d.year} a year ($${d.permonth} a month)`;
    }
  };
  bills.forEach((b) => b.addEventListener("click", () => setBill(b.dataset.bill)));

  // ---- what you get: the strip follows the plan under the pointer (or the one tapped)
  const gets = document.getElementById("gets");
  let shown = "";
  const show = (id) => {
    if (id === shown) return;
    shown = id;
    plans.forEach((p) => p.classList.toggle("lit", p.dataset.plan === id));
    document.getElementById("gets-name").textContent = NAMES[id];
    for (const k of ["q", "m", "t", "b"]) {
      gets.querySelector(`u[data-k="${k}"]`).style.transform = `scaleX(${(LIMITS[id][k] / TOP[k]).toFixed(4)})`;
      gets.querySelector(`b[data-k="${k}"]`).textContent = LIMITS[id][k];
    }
    const card = plans.find((p) => p.dataset.plan === id);
    if (!reduced) window.pyxi?.fly?.point?.(card.querySelector("h2"), NAMES[id], { hold: 1400 });
  };
  plans.forEach((p) => {
    p.addEventListener("pointerenter", () => show(p.dataset.plan));
    p.addEventListener("focusin", () => show(p.dataset.plan));
    p.addEventListener("click", () => show(p.dataset.plan));
  });
  show("pro");

  // ---- the jars
  const jars = plans.map((p, k) => {
    const canvas = p.querySelector("canvas");
    const n = Number(p.dataset.flies);
    let seed = 7 + k * 31;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
    const flies = Array.from({ length: n }, () => ({ x: 0.2 + rnd() * 0.6, y: 0.3 + rnd() * 0.55, a: rnd() * 6.28, s: 0.3 + rnd() * 0.7, r: 1.6 + rnd() * 1.6 }));
    return { p, canvas, flies, x: canvas.getContext("2d") };
  });
  const drawJar = (j, t) => {
    const dpr = Math.min(2, devicePixelRatio || 1);
    const w = j.canvas.clientWidth;
    const h = j.canvas.clientHeight;
    if (j.canvas.width !== Math.round(w * dpr)) {
      j.canvas.width = Math.round(w * dpr);
      j.canvas.height = Math.round(h * dpr);
    }
    const x = j.x;
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    x.clearRect(0, 0, w, h);
    const lit = j.p.classList.contains("lit") ? 1 : 0.7;
    for (const f of j.flies) {
      const fx = (f.x + Math.sin(t * 0.5 * f.s + f.a) * 0.07) * w;
      const fy = (f.y + Math.cos(t * 0.4 * f.s + f.a * 2) * 0.06) * h;
      const glow = (0.55 + 0.45 * Math.sin(t * 1.6 * f.s + f.a)) * lit;
      const g = x.createRadialGradient(fx, fy, 0, fx, fy, f.r * 7);
      g.addColorStop(0, `rgba(255, 216, 130, ${(0.5 * glow).toFixed(3)})`);
      g.addColorStop(1, "rgba(255, 200, 97, 0)");
      x.fillStyle = g;
      x.beginPath();
      x.arc(fx, fy, f.r * 7, 0, 7);
      x.fill();
      x.fillStyle = `rgba(255, 246, 214, ${(0.5 + 0.5 * glow).toFixed(3)})`;
      x.beginPath();
      x.arc(fx, fy, f.r, 0, 7);
      x.fill();
    }
  };
  let on = false;
  const tick = (ms) => {
    if (!on) return;
    jars.forEach((j) => drawJar(j, ms / 1000));
    requestAnimationFrame(tick);
  };
  if (reduced) {
    const still = () => jars.forEach((j) => drawJar(j, 3));
    still();
    addEventListener("resize", still);
  } else
    new IntersectionObserver((e) => {
      const was = on;
      on = e[e.length - 1].isIntersecting && !document.hidden;
      if (on && !was) requestAnimationFrame(tick);
    }).observe(document.getElementById("plans"));
}
