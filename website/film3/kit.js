// The demo clips' shared machinery. A scene page gives its screen, its plan and its draw();
// this file builds the stage round it and exposes the two calls render.py makes:
//
//   setup(cfg)  lay the clip out on the beat grid from the real lengths of Pyxi's lines
//   draw(t)     put every element where it is at t seconds
//
// The picture is a pure function of time. Nothing here uses a timer, a CSS animation or the
// clock, so the renderer can step it one frame at a time and no frame is ever late.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const clamp = (x) => Math.max(0, Math.min(1, x));
const seg = (t, a, b) => clamp((t - a) / (b - a));
const out3 = (u) => 1 - Math.pow(1 - u, 3);
const expo = (u) => (u >= 1 ? 1 : 1 - Math.pow(2, -10 * u));
const back = (u) => 1 + 2.2 * Math.pow(u - 1, 3) + 1.2 * Math.pow(u - 1, 2); // lands a little past, then settles
const io3 = (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2);
const mix = (a, b, u) => a + (b - a) * u;
const BEAT = 0.5; // 120 beats a minute
const onBeat = (t) => Math.ceil(t / BEAT - 1e-6) * BEAT;
const svg = (tag, attrs = {}) => { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };

/** A value that moves between keys: [{at, d, v, ease}] means "from `at`, take `d` seconds to reach v". */
function keyed(t, keys) {
  let v = keys[0].v;
  for (const k of keys) {
    if (t <= k.at) break;
    const u = (k.ease || io3)(seg(t, k.at, k.at + (k.d || 0.001)));
    v = Array.isArray(v) ? v.map((x, i) => mix(x, k.v[i], u)) : mix(v, k.v, u);
  }
  return v;
}

const FLY = `<svg viewBox="-24 -24 48 48"><defs>
  <radialGradient id="ff-tail" cx="0.45" cy="0.4" r="0.65"><stop offset="0" stop-color="#fffbea"/><stop offset="0.55" stop-color="#ffe08a"/><stop offset="1" stop-color="#ffb02e"/></radialGradient>
  <radialGradient id="ff-halo"><stop offset="0" stop-color="#ffe08a" stop-opacity="0.6"/><stop offset="0.45" stop-color="#ffc861" stop-opacity="0.22"/><stop offset="1" stop-color="#ffc861" stop-opacity="0"/></radialGradient></defs>
  <circle class="ff-halo" r="13" fill="url(#ff-halo)"/>
  <g transform="rotate(-30)">
    <path class="ff-feeler" d="M-0.7 -9.2 Q-1.6 -11.6 -3.4 -12.6 M0.7 -9.2 Q1.6 -11.6 3.4 -12.6"/>
    <g transform="rotate(-38 -2.4 -5)"><ellipse class="ff-wing" cx="-2.4" cy="-5" rx="3.8" ry="1.7"/></g>
    <g transform="rotate(38 2.4 -5)"><ellipse class="ff-wing" cx="2.4" cy="-5" rx="3.8" ry="1.7"/></g>
    <ellipse class="ff-body" cx="0" cy="-5" rx="2.3" ry="2.6"/><circle class="ff-body" cx="0" cy="-8.3" r="1.6"/>
    <ellipse class="ff-core" cx="0" cy="0" rx="3" ry="3.7" fill="url(#ff-tail)"/>
  </g></svg>`;
const MARK = `<svg class="n-mark" viewBox="-7 -7 14 14"><circle class="halo" r="6.5"/><circle class="core" r="2.6"/></svg>`;
const WAVE = (small) => `<div class="n-wave${small ? " small" : ""}">${"<i></i>".repeat(7)}</div>`;
/** What the notch shows, by state: the same rows as the app's Notch.tsx. */
const Notch = {
  idle: (at) => ({ at, cls: "idle", html: "" }),
  listen: (at) => ({ at, html: `${MARK}<span class="n-text strong">Listening</span>${WAVE()}`, wave: "mic" }),
  look: (at, text) => ({ at, html: `${MARK}<span class="n-text shimmer">${text}</span>` }),
  say: (at, text) => ({ at, cls: "top", html: `${MARK}<div class="n-say">${text}</div>${WAVE(true)}`, wave: "voice" }),
  work: (at, who, what, color) => ({ at, html: `<span class="n-marks"><i style="--c:${color}"></i></span><span class="n-text"><b>${who}</b> ${what}</span>` }),
  result: (at, title, body) => ({ at, cls: "result", html: `<div class="r-head"><i></i><b>${title}</b></div><p class="r-body">${body}</p>` }),
};

const Film = { T: null, C: null, S: null };

Film.scene = function (S) {
  Film.S = S;
  const story = window.STORY[S.id];
  document.body.innerHTML = `<div id="stage">
    <div class="glow" id="g1"></div><div class="glow" id="g2"></div><div id="vig"></div>
    <div class="type" id="typeA"><div class="slam" id="a1">${story.problem[0]}</div><div class="slam" id="a2">${story.problem[1]}</div></div>
    <div id="cam"><div id="world">
      <div id="win">${S.screen}</div>
      ${S.overlay || ""}
      <div id="notch"></div>
      <svg id="ptr" viewBox="0 0 26 26"><path d="M3 2 L3 21 L8.2 16.4 L11.6 24 L14.8 22.6 L11.5 15.2 L18.4 15.2 Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>
      <div id="tapper" class="ov"><div id="tap"></div></div>
      <div id="fly">${FLY}</div>
    </div></div>
    <div id="flash"></div>
    <div class="type" id="typeE"><div class="slam" id="e1">${story.lesson[0]}</div><div class="slam" id="e2">${story.lesson[1]}</div><div id="how">${story.how}</div></div>
    <div class="type" id="typeR"><div id="rhead">${window.STORY.recap.head}</div><div id="rrows">${window.STORY.recap.rows.map((r) => `<div class="rrow"><i><svg viewBox="0 0 24 24"><path d="M5 12.5 L10 17.5 L19 7"/></svg></i><span>${r}</span></div>`).join("")}</div></div>
    <div class="type" id="typeC"><div id="name"><div class="mark"></div><div class="word">${window.STORY.name}</div></div><div id="tag">${window.STORY.tag}</div><div id="where"></div></div>
    <div id="fine">${window.STORY.fine}</div>
    <div id="band"></div><div id="cap"></div>
  </div>`;
  for (const id of ["a1", "a2", "e1", "e2"]) if ($("#" + id).textContent.length > 15) $("#" + id).classList.add("long");
};

/** Lay the clip out. cfg: { durs: seconds of each spoken line, env: the voice's loudness a
 * frame, mode: "clip" | "loop" | "hero" | "heroEnd", flashIn: start out of the last scene's light }. */
function setup(cfg = {}) {
  const S = Film.S, story = window.STORY[S.id];
  const C = (Film.C = Object.assign({ mode: "clip", env: [], flashIn: false }, cfg));
  C.durs = C.durs || story.says.map((s) => 0.4 + s.length * 0.062);
  // the opening is the same in every clip: two lines land, the camera flies through them,
  // the screen arrives, Pyxi listens, hears the ask, and looks
  const B = { a1: 0.5, a2: 1.0, b: 2.0, listen: 2.5 };
  // the ask is spoken by the person, in a voice of their own: its real length when the renderer gives it
  B.words = [2.7, 2.7 + (C.askDur ? C.askDur + 0.15 : Math.max(0.9, story.ask.length * 0.04))];
  B.look = onBeat(B.words[1] + 0.1);
  B.act = B.look + BEAT;
  const T = (Film.T = Object.assign(B, S.plan(C.durs, B)));
  // after the screen: the lesson (E seconds), in the film's last scene the recap (R), then the name
  T.E = 2.5; T.R = C.mode === "heroEnd" ? 4.0 : 0;
  T.r = T.c + T.E; T.n = T.r + T.R;
  T.total = C.mode === "clip" ? T.n + 2.75 : C.mode === "heroEnd" ? T.n + 3.5 : C.mode === "hero" ? T.r : T.c;
  T.phrases = story.says.map((text, i) => ({ text, at: T.voice[i], until: T.voice[i] + C.durs[i] + 0.35 }));
  T.phrases.forEach((p, i) => { const n = T.phrases[i + 1]; if (n) p.until = Math.min(p.until, n.at - 0.04); else p.until = Math.min(p.until, T.c - 0.5); });
  $("#where").textContent = C.mode === "heroEnd" ? window.STORY.heroEnd : story.payoff;

  // measure with nothing transformed
  $("#world").style.transform = "none"; $("#cam").style.transform = "none";
  for (const id of ["typeA", "typeC", "typeE", "typeR"]) $("#" + id).style.display = "none";
  const notch = $("#notch");
  Film.notch = S.notch(T, Notch);
  notch.innerHTML = Film.notch.map((n) => `<div class="nrow ${n.cls || ""}">${n.html}</div>`).join("");
  Film.notch.forEach((n, i) => { const r = notch.children[i].getBoundingClientRect(); n.w = r.width; n.h = r.height; n.el = notch.children[i]; });
  if (S.init) S.init(T);
  for (const id of ["typeA", "typeC", "typeE", "typeR"]) $("#" + id).style.display = "";
  draw(0);
  return {
    T, total: T.total, says: story.says, ask: story.ask,
    fonts: ['800 176px "Inter"', '600 33px "Fraunces"', 'italic 500 84px "Fraunces"'].map((f) => document.fonts.check(f)),
  };
}

// a word that lands: big and soft, then sharp and still
function slam(el, t, at) {
  const u = expo(seg(t, at, at + 0.32));
  el.style.opacity = seg(t, at, at + 0.06);
  el.style.transform = `scale(${mix(1.42, 1, u)})`;
  el.style.filter = u < 0.98 ? `blur(${(1 - u) * 18}px)` : "none";
}

function drawNotch(t, loud) {
  const N = Film.notch, el = $("#notch");
  let i = 0;
  for (let k = 0; k < N.length; k++) if (t >= N[k].at) i = k;
  const cur = N[i], prev = N[Math.max(0, i - 1)];
  const u = i === 0 ? 1 : back(seg(t, cur.at, cur.at + 0.36));
  const w = mix(prev.w, cur.w, u), h = mix(prev.h, cur.h, u);
  el.style.width = w + "px"; el.style.height = h + "px"; el.style.left = 960 - w / 2 + "px";
  el.style.borderRadius = `0 0 ${Math.min(28, h * 0.5)}px ${Math.min(28, h * 0.5)}px`;
  N.forEach((n, k) => { n.el.style.opacity = k === i ? (i === 0 ? 1 : seg(t, cur.at + 0.08, cur.at + 0.24)) : k === i - 1 ? 1 - seg(t, cur.at, cur.at + 0.1) : 0; });
  for (const sh of $$(".shimmer", cur.el)) sh.style.backgroundPosition = `${-(t * 140) % 220}% 0`;
  const bars = $$(".n-wave i", cur.el);
  bars.forEach((b, k) => {
    const mic = 0.25 + 0.75 * Math.abs(Math.sin(t * (9 + k * 1.7) + k * 2)) * (t < Film.T.words[1] + 0.15 ? 1 : 0.2);
    const v = cur.wave === "mic" ? mic : 0.18 + 0.82 * loud * (0.55 + 0.45 * Math.abs(Math.sin(t * 17 + k * 1.3)));
    b.style.transform = `scaleY(${Math.max(0.18, v)})`;
  });
  const halo = $(".n-mark .halo", cur.el);
  if (halo) halo.style.opacity = 0.55 + 0.45 * Math.sin(t * 5.2) ** 2;
}

function draw(t) {
  const S = Film.S, T = Film.T, C = Film.C, P = T.c;
  const hasC = C.mode === "clip" || C.mode === "heroEnd";
  const shot = t < T.b ? "a" : t < T.c || C.mode === "loop" ? "b" : t < T.r ? "e" : t < T.n ? "r" : hasC ? "c" : "e";
  const typeE = $("#typeE"), typeR = $("#typeR");
  typeE.style.visibility = shot === "e" ? "visible" : "hidden";
  typeR.style.visibility = shot === "r" ? "visible" : "hidden";
  const typeA = $("#typeA"), typeC = $("#typeC"), cam = $("#cam"), world = $("#world"), flash = $("#flash"), band = $("#band"), cap = $("#cap");
  typeA.style.visibility = shot === "a" ? "visible" : "hidden";
  cam.style.visibility = shot === "b" ? "visible" : "hidden";
  typeC.style.visibility = shot === "c" ? "visible" : "hidden";
  $("#fine").style.opacity = 0;

  // the backdrop never stops moving, and comes back to where it began when the scene ends, so a loop has no seam
  const w = (2 * Math.PI) / P;
  $("#g1").style.transform = `translate(${-520 + 160 * Math.sin(t * w)}px, ${260 + 120 * Math.cos(t * w)}px) scale(${shot === "c" ? 1.25 : 1})`;
  $("#g2").style.transform = `translate(${980 + 170 * Math.cos(t * w + 1)}px, ${-640 + 130 * Math.sin(t * w * 2)}px)`;

  if (shot === "a") {
    slam($("#a1"), t, T.a1);
    slam($("#a2"), t, T.a2);
    // the way out: the camera flies through the words
    const through = Math.pow(seg(t, T.b - 0.34, T.b), 3);
    typeA.style.transform = `scale(${1 + 0.03 * seg(t, 0, T.b) + 6 * through})`;
    typeA.style.filter = through > 0 ? `blur(${through * 16}px)` : "none";
    typeA.style.opacity = 1 - seg(t, T.b - 0.12, T.b);
    band.style.opacity = cap.style.opacity = 0;
    // in the film, a scene opens out of the light the last one ended in
    flash.style.setProperty("--x", "960px"); flash.style.setProperty("--y", "540px");
    flash.style.opacity = C.flashIn ? 1 - out3(seg(t, 0.5, 0.95)) : 0;
    return;
  }
  if (shot === "e") {
    // the lesson: the light the camera flew into clears, and what Pyxi just did is said in big words
    const s = t - T.c, E = T.E;
    flash.style.setProperty("--x", "960px"); flash.style.setProperty("--y", "540px");
    flash.style.opacity = 1 - out3(seg(s, 0, 0.4));
    slam($("#e1"), t, T.c + 0.05);
    slam($("#e2"), t, T.c + 0.5);
    const h = out3(seg(s, 1.0, 1.35));
    $("#how").style.opacity = h; $("#how").style.transform = `translateY(${20 * (1 - h)}px)`;
    const through = Math.pow(seg(s, E - 0.34, E), 3);
    typeE.style.transform = `scale(${1 + 0.03 * seg(s, 0, E) + 6 * through})`;
    typeE.style.filter = through > 0 ? `blur(${through * 16}px)` : "none";
    typeE.style.opacity = 1 - seg(s, E - 0.12, E);
    band.style.opacity = cap.style.opacity = 0;
    return;
  }
  if (shot === "r") {
    // the film's last list: the four lessons, one on every beat
    const s = t - T.r, R = T.R;
    flash.style.opacity = 0;
    const h = out3(seg(s, 0.0, 0.35));
    $("#rhead").style.opacity = h; $("#rhead").style.transform = `translateY(${18 * (1 - h)}px)`;
    $$("#rrows .rrow").forEach((row, k) => {
      const at = 0.5 + k * 0.5, u = expo(seg(s, at, at + 0.4));
      row.style.opacity = seg(s, at, at + 0.08); row.style.transform = `translateX(${mix(70, 0, u)}px)`;
      row.querySelector("i").style.transform = `scale(${back(seg(s, at + 0.1, at + 0.45))})`;
    });
    const through = Math.pow(seg(s, R - 0.34, R), 3);
    typeR.style.transform = `scale(${1 + 0.02 * seg(s, 0, R) + 5 * through})`;
    typeR.style.filter = through > 0 ? `blur(${through * 16}px)` : "none";
    typeR.style.opacity = 1 - seg(s, R - 0.12, R);
    band.style.opacity = cap.style.opacity = 0;
    return;
  }
  if (shot === "c") {
    const s = t - T.n;
    flash.style.opacity = 0;
    const u = expo(seg(s, 0, 0.36));
    $("#name").style.opacity = seg(s, 0, 0.05);
    $("#name").style.transform = `scale(${mix(1.5, 1, u)})`;
    $("#name").style.filter = u < 0.98 ? `blur(${(1 - u) * 20}px)` : "none";
    $("#name .mark").style.transform = `scale(${1 + 0.05 * Math.sin(s * 5)})`;
    const v = out3(seg(s, 0.5, 0.85));
    $("#tag").style.opacity = v; $("#tag").style.transform = `translateY(${24 * (1 - v)}px)`;
    $("#where").style.opacity = out3(seg(s, 1.0, 1.4));
    $("#fine").style.opacity = out3(seg(s, 1.2, 1.6));
    typeC.style.transform = `scale(${1 + 0.025 * seg(s, 0, 3.5)})`;
    band.style.opacity = cap.style.opacity = 0;
    return;
  }

  // ---- the screen
  const loud = clamp(C.env[Math.floor(t * 60)] || 0); // the real voice makes the firefly glow
  const st = S.draw(t, T, loud);
  const [px, py] = st.ptr;
  $("#ptr").style.transform = `translate(${px}px, ${py}px) scale(${1 - 0.12 * tapNow(t, T.taps, 0.16)})`;
  // a click: a ring opens round the pointer's tip
  const tp = tapNow(t, T.taps, 0.45);
  $("#tapper").style.transform = `translate(${px + 3}px, ${py + 2}px)`;
  $("#tap").style.opacity = tp > 0 ? 1 - (1 - tp) : 0;
  $("#tap").style.transform = `scale(${mix(1.25, 0.3, tp)})`;
  const bob = 3.5 * Math.sin(t * 2.6), sway = 2.5 * Math.sin(t * 1.7 + 1);
  const fx = (st.fly ? st.fly[0] : px + 44) + sway, fy = (st.fly ? st.fly[1] : py + 42) + bob;
  $("#fly").style.transform = `translate(${fx}px, ${fy}px)`;
  const listening = seg(t, T.listen, T.listen + 0.2) * (1 - seg(t, T.look - 0.2, T.look));
  $("#fly .ff-halo").style.transform = `scale(${1 + 0.07 * Math.sin(t * 5) + 0.3 * listening + 0.9 * loud})`;
  const flap = 0.62 + 0.38 * Math.sin(t * 46);
  for (const wg of $$("#fly .ff-wing")) wg.style.transform = `scaleY(${flap})`;
  drawNotch(t, loud);

  // the camera: comes in at an angle from far away, then goes where the scene sends it
  const arrive = expo(seg(t, T.b, T.b + 1.0));
  const c = st.cam;
  const ry = mix(-24, 0, arrive) + c.ry + 1.1 * Math.sin(t * 0.7), rx = mix(10, 0, arrive) + c.rx + 0.4 * Math.cos(t * 0.6);
  const sc = c.s * mix(0.3, 1, arrive);
  world.style.transformOrigin = `${c.x}px ${c.y}px`;
  world.style.transform = `translate3d(${960 - c.x + (c.ox || 0)}px, ${520 - c.y + (c.oy || 0) + mix(40, 0, arrive)}px, 0) rotateY(${ry}deg) rotateX(${rx}deg) scale(${sc})`;
  cam.style.opacity = seg(t, T.b, T.b + 0.1);
  cam.style.transform = "none"; cam.style.filter = "none";

  if (C.mode === "loop") {
    // a loop ends by letting the screen fall back into the dark it came from
    const away = io3(seg(t, T.c - 0.5, T.c - 0.04));
    cam.style.opacity = 1 - away;
    cam.style.transform = `scale(${1 - 0.06 * away})`;
    if (away > 0) cam.style.filter = `blur(${away * 10}px)`;
    flash.style.opacity = 0;
  } else {
    // the way out: the camera flies into the firefly, and its light fills the frame
    const into = Math.pow(seg(t, T.c - 0.45, T.c), 3);
    if (into > 0) {
      const r = $("#fly").getBoundingClientRect();
      cam.style.transformOrigin = `${r.left}px ${r.top}px`;
      cam.style.transform = `scale(${1 + 18 * into})`;
      flash.style.setProperty("--x", r.left + "px"); flash.style.setProperty("--y", r.top + "px");
    }
    flash.style.opacity = seg(t, T.c - 0.2, T.c - 0.02);
  }

  // captions: the ask in quotes, then Pyxi's own words, a line at a time
  let text = "", on = 0, who = "";
  const askOff = T.voice[0] - 0.1;
  if (t >= T.words[0] - 0.1 && t < askOff) { text = `“${window.STORY[S.id].ask}”`; who = "you"; on = seg(t, T.words[0] - 0.1, T.words[0] + 0.1) * (1 - seg(t, askOff - 0.2, askOff)); }
  for (const p of T.phrases) if (t >= p.at && t < p.until) { text = p.text; who = "pyxi"; on = seg(t, p.at, p.at + 0.12) * (1 - seg(t, p.until - 0.14, p.until)); }
  const key = who + text;
  if (cap.dataset.key !== key) {
    cap.dataset.key = key;
    const label = who === "you" ? `<span class="keys">${window.STORY.intro.keys.map((k) => `<kbd>${k}</kbd>`).join("")}</span>${window.STORY.you}` : `<i></i>${window.STORY.name}`;
    cap.innerHTML = text ? `<div class="who ${who}">${label}</div><div class="line"></div>` : "";
    if (text) cap.querySelector(".line").textContent = text;
  }
  cap.style.opacity = on;
  band.style.opacity = seg(t, T.words[0] - 0.4, T.words[0] - 0.1) * (1 - seg(t, T.c - 0.9, T.c - 0.6));
}

/** 1 at the moment of the latest click, falling to 0 over `span` seconds. */
function tapNow(t, taps = [], span) {
  let v = 0;
  for (const at of taps) if (t >= at && t < at + span) v = 1 - (t - at) / span;
  return v;
}

/** Where the pointer is: it rests at each stop and glides to the next. stops: [{at, d, x, y}]. */
function pointer(t, stops) {
  let [x, y] = [stops[0].x, stops[0].y];
  for (const s of stops) {
    if (t <= s.at) break;
    const u = io3(seg(t, s.at, s.at + s.d));
    const lift = -22 * Math.sin(Math.PI * u) * Math.min(1, Math.hypot(s.x - x, s.y - y) / 400);
    x = mix(x, s.x, u); y = mix(y, s.y, u) + lift;
  }
  return [x, y];
}

/** An element's box in the world's own coordinates (call only from init(), when nothing is transformed). */
function boxOf(el) {
  const r = (typeof el === "string" ? $(el) : el).getBoundingClientRect();
  return { x: r.left, y: r.top, w: r.width, h: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2 };
}
