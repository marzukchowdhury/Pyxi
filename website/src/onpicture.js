/**
 * "Explains any picture" on the home page: a replay of how Pyxi explains a diagram by writing
 * on it. It follows the app (desktop/src/renderer/src/overlay/NotesCard.tsx, PictureTags, and
 * the explain_on_screen 'on_picture' rules in src/pyxi/companion/prompts.py): you ask out loud,
 * the notch shows what Pyxi is saying, and each point is a small red tag pinned to a real part
 * of the picture with a leader and a dot, said in the order things happen, with the route
 * between parts traced along the picture as it is said.
 *
 * The picture is a drawing made for this page (partials/heart.html, 600 x 660 units); the
 * points below are in its units. Reduced motion: every tag and route is simply there, and all
 * the lines are written out.
 */
const SVG = "http://www.w3.org/2000/svg";
const W = 600;
const H = 660;
const OPEN = "It's the heart, opened up so you can see its four chambers. Follow the blood.";
const CLOSE =
  "Both sides squeeze together, about once a second, your whole life. Want me to build it in 3D?";
const STEPS = [
  {
    part: "Superior vena cava",
    at: [206, 132],
    tag: [98, 92],
    route: [
      [206, 50],
      [206, 132],
      [206, 216],
    ],
    say: "Blood low on oxygen comes back from the body through this vein.",
  },
  {
    part: "Right atrium",
    at: [226, 306],
    tag: [86, 296],
    route: [
      [206, 216],
      [210, 266],
      [226, 306],
    ],
    say: "It gathers here, in the right atrium.",
  },
  {
    part: "Right ventricle",
    at: [262, 452],
    tag: [84, 470],
    route: [
      [226, 306],
      [232, 362],
      [262, 452],
    ],
    say: "Then it drops through a valve into the right ventricle,",
  },
  {
    part: "Pulmonary artery",
    at: [432, 211],
    tag: [520, 168],
    route: [
      [262, 452],
      [286, 392],
      [284, 300],
      [314, 232],
      [400, 210],
      [506, 215],
    ],
    say: "which pumps it out to the lungs to pick up oxygen.",
  },
  {
    part: "Left atrium",
    at: [426, 300],
    tag: [532, 392],
    route: [
      [546, 303],
      [486, 303],
      [426, 300],
    ],
    say: "It comes back bright red into the left atrium,",
  },
  {
    part: "Left ventricle",
    at: [410, 462],
    tag: [524, 522],
    route: [
      [426, 300],
      [416, 362],
      [410, 462],
    ],
    say: "falls into the left ventricle, the strongest chamber,",
  },
  {
    part: "Aorta",
    at: [400, 112],
    tag: [296, 62],
    route: [
      [410, 462],
      [362, 392],
      [340, 312],
      [334, 192],
      [400, 112],
      [466, 160],
      [478, 252],
    ],
    say: "and is pushed out through the aorta to the whole body.",
  },
];

/** Smooth path through points (Catmull-Rom as cubic Beziers), as the app draws its routes. */
function through(pts) {
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C ${c1[0].toFixed(1)} ${c1[1].toFixed(1)}, ${c2[0].toFixed(1)} ${c2[1].toFixed(1)}, ${p2[0]} ${p2[1]}`;
  }
  return d;
}
function arrowHead(route) {
  const [ex, ey] = route[route.length - 1];
  const [px, py] = route[route.length - 2];
  const a = Math.atan2(ey - py, ex - px);
  const r = 16;
  return `M ${(ex - r * Math.cos(a - 0.5)).toFixed(1)} ${(ey - r * Math.sin(a - 0.5)).toFixed(1)} L ${ex} ${ey} L ${(ex - r * Math.cos(a + 0.5)).toFixed(1)} ${(ey - r * Math.sin(a + 0.5)).toFixed(1)}`;
}
const reading = (text) => Math.max(2500, text.split(/\s+/).length * 290 + 900);

export function initOnPicture({ reduced = false } = {}) {
  const el = document.getElementById("op");
  if (!el) return;
  const $ = (s) => el.querySelector(s);
  const ink = $(".op-ink");
  const tags = $(".op-tags");
  const notch = $(".op-notch");
  const notchText = $(".op-notch-text");
  const lines = $(".op-lines");
  const you = $(".op-you b");
  const ASK = you.textContent;
  let run = 0; // every replay or jump gets a new number; older timers see it and stop
  let shown = -1;

  // the lines Pyxi says, as a list you can click
  const row = (text, label, onClick) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    const n = document.createElement("i");
    n.textContent = label;
    const span = document.createElement("span");
    span.textContent = text;
    btn.append(n, span);
    btn.addEventListener("click", onClick);
    li.append(btn);
    return li;
  };
  const rows = [
    row(OPEN, "", () => play(-1)),
    ...STEPS.map((s, i) => row(s.say, String(i + 1), () => play(i))),
    row(CLOSE, "", () => play(STEPS.length)),
  ];
  lines.append(...rows);

  const say = (text, state) => {
    notchText.textContent = text;
    notch.dataset.state = state;
  };
  const mark = (i) => rows.forEach((li, k) => li.classList.toggle("on", k <= i + 1));
  const now = (i) => rows.forEach((li, k) => li.classList.toggle("now", k === i + 1));

  /** Pin step i's tag to its part and trace the route that leads there. */
  function reveal(i, quick = false) {
    if (i <= shown) return;
    for (let k = shown + 1; k <= i; k++) {
      const s = STEPS[k];
      const g = document.createElementNS(SVG, "g");
      if (quick && k < i) g.setAttribute("class", "quick");
      const path = (cls, d) => {
        const p = document.createElementNS(SVG, "path");
        p.setAttribute("class", cls);
        p.setAttribute("d", d);
        p.setAttribute("pathLength", "1");
        g.append(p);
      };
      path("op-route", through(s.route));
      path("op-route head", arrowHead(s.route));
      path("op-leader", `M ${s.tag[0]} ${s.tag[1]} L ${s.at[0]} ${s.at[1]}`);
      const dot = document.createElementNS(SVG, "circle");
      dot.setAttribute("class", "op-dot");
      dot.setAttribute("cx", s.at[0]);
      dot.setAttribute("cy", s.at[1]);
      dot.setAttribute("r", "6.5");
      g.append(dot);
      ink.append(g);
      const tag = document.createElement("span");
      tag.className = "op-tag";
      tag.style.left = `${(s.tag[0] / W) * 100}%`;
      tag.style.top = `${(s.tag[1] / H) * 100}%`;
      tag.textContent = s.part;
      tags.append(tag);
    }
    shown = i;
    ink.querySelectorAll(".op-dot").forEach((d, k) => d.classList.toggle("live", k === i));
  }
  function clear() {
    ink.replaceChildren();
    tags.replaceChildren();
    shown = -1;
  }

  /** Play from a point: -2 the very start (you asking), -1 Pyxi's first line, n a step, then the close. */
  function play(from = -2) {
    const me = ++run;
    const wait = (ms) =>
      new Promise((done) => setTimeout(() => me === run && done(), reduced ? 0 : ms));
    el.classList.remove("is-done");
    (async () => {
      if (from <= -1 || from < shown) clear();
      if (from === -2) {
        mark(-2);
        now(-9);
        el.classList.add("is-asking");
        say("Listening", "listening");
        you.textContent = "";
        for (let c = 1; c <= ASK.length; c++) {
          you.textContent = ASK.slice(0, c);
          await wait(34);
        }
        await wait(700);
        say("Thinking", "thinking");
        await wait(800);
      }
      you.textContent = ASK;
      el.classList.remove("is-asking");
      if (from <= -1) {
        mark(-1);
        now(-1);
        say(OPEN, "speaking");
        await wait(reading(OPEN));
      }
      for (let i = Math.max(0, from); i < STEPS.length; i++) {
        reveal(i, i === from);
        mark(i);
        now(i);
        say(STEPS[i].say, "speaking");
        await wait(reading(STEPS[i].say));
      }
      reveal(STEPS.length - 1, true);
      mark(STEPS.length);
      now(STEPS.length);
      say(CLOSE, "speaking");
      await wait(reading(CLOSE));
      say("Ask me anything", "idle");
      now(-9);
      el.classList.add("is-done");
    })();
  }

  $('[data-act="replay"]').addEventListener("click", () => play(-2));
  if (reduced) {
    // nothing plays by itself: the finished explanation is simply there
    reveal(STEPS.length - 1, true);
    el.classList.add("is-still", "is-done");
    mark(STEPS.length);
    say("Ask me anything", "idle");
    return;
  }
  say("Hold Ctrl Alt and ask", "idle");
  new IntersectionObserver(
    (entries, o) => {
      if (entries.some((e) => e.isIntersecting)) {
        o.disconnect();
        play(-2);
      }
    },
    { threshold: 0.6 },
  ).observe($(".op-stage"));
}
