/**
 * The launch film's scenes, each a pure function of time (film2/film.html).
 *
 * The pictures are the storyboard's own (storyboard.js builds each beat of each scene, on a
 * laptop); this file plays them: the big type, the laptop arriving, the ask heard word by word,
 * each new state of the screen landing as Pyxi speaks, the camera moving the whole time, and the
 * dive into the firefly's light that ends the scene.
 *
 *   plan(id, voiceSeconds)   the scene's timeline, worked out on the half-second beat
 *   setScene(id, cfg)        build that scene; cfg carries the voice's loudness per frame
 *   draw(t)                  everything, for t seconds into the scene
 *
 * Nothing reads a clock and nothing is random.
 */
import "./storyboard.css";
import "./ram.css";
import "./film.css";
import { STORY } from "./story.js";
import { BEATS } from "./storyboard.js";

document.body.classList.add("laptop");
const $ = (id) => document.getElementById(id);
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const seg = (t, a, b) => clamp((t - a) / (b - a));
const out3 = (u) => 1 - (1 - u) ** 3;
const io = (u) => (u < 0.5 ? 4 * u * u * u : 1 - (-2 * u + 2) ** 3 / 2);
const mix = (a, b, u) => a + (b - a) * u;
const amber = (l) => l.replace(/\[(.+?)\]/g, "<em>$1</em>");
const BEAT = 0.5;
const onBeat = (t) => Math.ceil(t / BEAT - 1e-6) * BEAT;
const SLIDES = { open: [1.0, 1.5, 1.5, 1.5, 1.25, 1.75], quick: [1.0, 1.0, 1.0, 1.0, 1.5] };

$("fly").innerHTML = BEATS.bug;
let cur = null; // the scene being drawn

/** The scene's timeline. Story scenes: type, the ask, the work, Pyxi's reply, the result held. */
export function plan(id, voice = 0) {
  const s = STORY.find((x) => x.id === id);
  if (id === "end") {
    const name = 1.0;
    const card = name + voice + 0.9;
    return { kind: "slides", at: [0, 0.5, name, card], total: card + 2.6, replyAt: name + 0.25 };
  }
  if (SLIDES[id]) {
    const at = [];
    let t = 0;
    for (const d of SLIDES[id]) {
      at.push(t);
      t += d;
    }
    return { kind: "slides", at, total: t };
  }
  const lines = s.problem.length;
  const a = [0.25, 1.0].slice(0, lines);
  const b = lines === 2 ? 2.0 : 1.5;
  const listen = b + 0.75;
  const words = s.ask.split(" ").length;
  const askEnd = listen + Math.max(1.2, words * 0.2);
  const work = onBeat(askEnd + 0.35);
  const replyAt = work + 0.4;
  const states = BEATS[id](s).length - 1 - (hasEndType(id, s) && typeOnlyLast(id, s) ? 1 : 0); // screens after the type
  const at = [b - 0.15, work];
  for (let k = 2; k < states; k++) at.push(replyAt + (voice * 0.85 * (k - 1)) / (states - 1));
  const held = replyAt + voice + 0.9;
  const typeAt = hasEndType(id, s) ? onBeat(held) : null;
  const c = typeAt === null ? onBeat(held) : typeAt + 1.75;
  return { kind: "story", a, b, listen, askEnd, work, replyAt, at, typeAt, c, total: c, voice };
}
const hasEndType = (id, s) => !!s.payoff?.type;
const typeOnlyLast = (id, s) => {
  const last = BEATS[id](s).at(-1)[1];
  return last.trim().startsWith('<div class="type');
};

export function setScene(id, cfg = {}) {
  const s = STORY.find((x) => x.id === id);
  const p = plan(id, cfg.voice ?? 0);
  const beats = BEATS[id](s);
  const stills = $("stills");
  stills.replaceChildren();
  const layers = [];
  const grab = (el, sel) => {
    const e = el.querySelector(sel);
    return e ? { x: parseFloat(e.style.left) * 2, y: parseFloat(e.style.top) * 2 } : null;
  };
  let ptr = null;
  let flyTo = null;
  const list = p.kind === "story" ? beats.slice(1).filter(([, html]) => !html.trim().startsWith('<div class="type')) : beats;
  for (const [, html] of list) {
    const d = document.createElement("div");
    d.className = "still";
    d.innerHTML = html;
    if (p.kind === "story") {
      ptr ??= grab(d, ".ptr");
      const f = grab(d, ".fly.on");
      if (f && !flyTo) flyTo = f;
      d.querySelectorAll(".ptr, .pill, .cap, .fly, .type, .standin").forEach((e) => e.remove());
    }
    stills.append(d);
    layers.push(d);
  }
  const reply = s.reply ? s.reply.split(/(?<=[.?!])\s+/) : [];
  const chars = reply.reduce((n, x) => n + x.length, 0) || 1;
  let t = p.replyAt ?? 0;
  const phrases = reply.map((text) => {
    const d = ((cfg.voice ?? 0) * text.length) / chars;
    const ph = { text, at: t, until: t + d };
    t += d;
    return ph;
  });
  if (p.kind === "story") {
    $("slam1").innerHTML = `<span>${amber(s.problem[0])}</span>`;
    $("slam2").innerHTML = `<span>${amber(s.problem[1] ?? "")}</span>`;
    $("slam3").innerHTML = (s.payoff?.type ?? []).map((l) => `<span>${amber(l)}</span>`).join("");
    for (const [el, lines] of [[$("slam1"), [s.problem[0]]], [$("slam2"), [s.problem[1] ?? ""]], [$("slam3"), s.payoff?.type ?? [""]]]) {
      const longest = Math.max(1, ...lines.map((l) => l.replace(/[[\]]/g, "").length));
      el.style.fontSize = `${Math.min(176, Math.round(3000 / longest))}px`;
    }
  } else for (const id2 of ["slam1", "slam2", "slam3"]) $(id2).innerHTML = "";
  cur = { id, s, p, layers, phrases, ptr: ptr ?? { x: 1040, y: 600 }, flyTo: flyTo ?? { x: 1250, y: 250 }, env: cfg.env ?? [], flashIn: !!cfg.flashIn, words: (s.ask ?? "").split(" ") };
  return { plan: p, fonts: ['800 176px "Inter Variable"', '600 44px "Inter Variable"', '600 36px "Fraunces Variable"', 'italic 500 84px "Fraunces Variable"'].map((f) => document.fonts.check(f)) };
}

function slam(el, t, at, until, zoomOut = true) {
  const u = seg(t, at, at + 0.22);
  const leave = seg(t, until - 0.32, until);
  el.style.opacity = u * (1 - leave);
  el.style.transform = `scale(${(mix(1.28, 1, out3(u)) + (zoomOut ? leave * leave * 5 : 0)).toFixed(4)})`;
  el.style.filter = `blur(${(mix(16, 0, out3(u)) + leave * 14).toFixed(2)}px)`;
}

export function draw(t) {
  const { p, layers } = cur;
  $("g1").style.transform = `translate(${(-520 + Math.sin(t * 0.21) * 120).toFixed(1)}px, ${(260 + Math.cos(t * 0.17) * 90).toFixed(1)}px)`;
  $("g2").style.transform = `translate(${(900 + Math.cos(t * 0.19) * 140).toFixed(1)}px, ${(-900 + Math.sin(t * 0.23) * 100).toFixed(1)}px)`;
  const flash = $("flash");
  const world = $("world");
  if (p.kind === "slides") {
    // each still lands on its beat: a touch large and soft, then sharp
    let k = 0;
    p.at.forEach((at, i) => t >= at && (k = i));
    layers.forEach((d, i) => (d.style.opacity = i === k ? 1 : 0));
    const u = out3(seg(t, p.at[k], p.at[k] + 0.2));
    world.style.opacity = 1;
    world.style.transform = `scale(${mix(1.07, 1, u).toFixed(4)})`;
    world.style.filter = `blur(${mix(9, 0, u).toFixed(2)}px)`;
    for (const id of ["ptr", "pill", "fly", "cap", "band", "slam1", "slam2", "slam3"]) $(id).style.opacity = 0;
    flash.style.transform = "scale(4)";
    flash.style.opacity = cur.flashIn ? 1 - seg(t, 0, 0.45) : 0;
    return;
  }
  world.style.filter = "";
  // A: the type
  if (p.a.length === 2) {
    slam($("slam1"), t, p.a[0], p.a[1] + 0.1);
    slam($("slam2"), t, p.a[1], p.b + 0.1);
  } else {
    slam($("slam1"), t, p.a[0], p.b + 0.1);
    $("slam2").style.opacity = 0;
  }
  // B: the laptop arrives from far away, at an angle; in close for the ask; a slow push while Pyxi speaks
  const arrive = out3(seg(t, p.b - 0.15, p.b + 1.1));
  const leave = seg(t, p.c - 0.45, p.c);
  const typing = p.typeAt === null ? 0 : io(seg(t, p.typeAt - 0.3, p.typeAt + 0.1));
  const punch = io(seg(t, p.listen - 0.3, p.listen + 0.4)) * (1 - io(seg(t, p.work - 0.5, p.work + 0.3)));
  const push = io(seg(t, p.replyAt, p.replyAt + p.voice + 0.6)) * 0.16;
  const focus = punch > 0.01 ? cur.ptr : cur.flyTo;
  const zoom = 1 + 0.42 * punch + push;
  const scale = mix(0.42, 1, arrive) * zoom * (1 + leave * leave * 3.2);
  const fx0 = (1 - zoom) * (focus.x - 960) * 0.8;
  const fy0 = (1 - zoom) * (focus.y - 540) * 0.8;
  world.style.opacity = seg(t, p.b - 0.15, p.b + 0.2) * (1 - seg(t, p.c - 0.12, p.c)) * (1 - 0.8 * typing);
  world.style.transform = `perspective(2400px) translate(${fx0.toFixed(1)}px, ${fy0.toFixed(1)}px) scale(${scale.toFixed(4)}) rotateY(${(mix(-34, 0, arrive) + Math.sin(t * 0.4) * 1.2).toFixed(3)}deg) rotateX(${(mix(14, 0, arrive) + Math.cos(t * 0.33) * 0.7).toFixed(3)}deg)`;
  world.style.filter = typing > 0 ? `blur(${(typing * 10).toFixed(1)}px)` : "";
  // the screen's states: each lands as its moment comes
  let k = 0;
  p.at.forEach((at, i) => t >= at && i < layers.length && (k = i));
  layers.forEach((d, i) => {
    const o = i < k ? 1 - seg(t, p.at[k], p.at[k] + 0.25) : i === k ? (k === 0 ? 1 : seg(t, p.at[k], p.at[k] + 0.25)) : 0;
    d.style.opacity = i === k && k > 0 ? 1 : o; // the new state sits over the old one as it fades
    d.style.clipPath = "";
    d.style.zIndex = i === k ? 2 : 1;
    if (i === k && k > 0) d.style.opacity = seg(t, p.at[k], p.at[k] + 0.25);
    if (i === k - 1) d.style.opacity = 1 - seg(t, p.at[k] + 0.1, p.at[k] + 0.3);
  });
  // the pointer, the firefly beside it, the ask
  const working = io(seg(t, p.work - 0.3, p.work + 0.5));
  const px = cur.ptr.x + Math.sin(t * 0.8) * 6;
  const py = cur.ptr.y + Math.cos(t * 0.7) * 5;
  $("ptr").style.opacity = 1;
  $("ptr").style.transform = `translate(${px.toFixed(1)}px, ${py.toFixed(1)}px)`;
  const said = clamp((t - p.listen) / (p.askEnd - p.listen)) * cur.words.length;
  const pill = $("pill");
  pill.style.opacity = seg(t, p.listen - 0.1, p.listen + 0.15) * (1 - seg(t, p.work - 0.3, p.work - 0.05));
  pill.style.transform = `translate(${Math.min(px + 60, 1500 - cur.words.join(" ").length * 13).toFixed(1)}px, ${(py + 40).toFixed(1)}px)`;
  pill.querySelector("span").innerHTML = cur.words.map((w, i) => (i < said ? w : `<i>${w}</i>`)).join(" ");
  pill.querySelectorAll("s").forEach((s, i) => (s.style.height = `${(8 + 20 * Math.abs(Math.sin(t * 9 + i * 1.7)) * (said < cur.words.length ? 1 : 0.2)).toFixed(1)}px`));
  const fx = mix(px + 44, cur.flyTo.x, working);
  const fy = mix(py + 6, cur.flyTo.y, working) + Math.sin(t * 2.1) * 5;
  const loud = cur.env[Math.round(t * 60)] ?? 0;
  $("fly").style.opacity = 1;
  $("fly").style.transform = `translate(${fx.toFixed(1)}px, ${fy.toFixed(1)}px) scale(${(1 + loud * 0.5 + leave * 18).toFixed(3)})`;
  $("fly").style.filter = `drop-shadow(0 0 ${(6 + loud * 14).toFixed(1)}px #ffd27a) drop-shadow(0 0 ${(18 + loud * 46).toFixed(1)}px rgba(255, 190, 80, 0.9))`;
  // captions
  let text = "";
  let o = 0;
  if (t >= p.listen + 0.2 && t < p.work - 0.05) {
    text = `“${cur.s.ask}”`;
    o = seg(t, p.listen + 0.2, p.listen + 0.45) * (1 - seg(t, p.work - 0.3, p.work - 0.05));
  }
  for (const ph of cur.phrases)
    if (t >= ph.at && t < ph.until + 0.25) {
      text = ph.text;
      o = seg(t, ph.at, ph.at + 0.12) * (1 - seg(t, ph.until + 0.1, ph.until + 0.25));
    }
  $("cap").textContent = text;
  $("cap").style.fontSize = text.length > 70 ? "34px" : "44px";
  $("cap").style.opacity = o * (1 - typing);
  $("band").style.opacity = Math.max(o, seg(t, p.listen, p.listen + 0.3) * (1 - seg(t, p.c - 0.4, p.c))) * (1 - typing);
  // the payoff line, as type over the dimmed screen
  if (p.typeAt !== null) slam($("slam3"), t, p.typeAt, p.c + 0.1, false);
  else $("slam3").style.opacity = 0;
  // out: into the light. In: the light clears.
  flash.style.setProperty("--x", `${fx.toFixed(0)}px`);
  flash.style.setProperty("--y", `${fy.toFixed(0)}px`);
  flash.style.transform = `scale(${(1 + seg(t, p.c - 0.3, p.c) * 3).toFixed(3)})`;
  flash.style.opacity = Math.max(seg(t, p.c - 0.3, p.c), cur.flashIn ? 1 - seg(t, 0, 0.45) : 0);
  if (cur.flashIn && t < 0.45) flash.style.transform = "scale(4)";
}

Object.assign(window, { plan, setScene, draw, STORY });
const q = new URLSearchParams(location.search);
document.fonts.ready.then(() => {
  document.body.dataset.film = "1";
  if (q.get("scene")) {
    setScene(q.get("scene"), { voice: Number(q.get("voice") ?? 4.5) });
    draw(Number(q.get("t") ?? 0));
  }
});
