/**
 * The team page. Its hero is the orbit: Pyxi at the centre, twelve teammates on three tilted
 * rings in real 3D (CSS perspective, positions computed here every frame so each disc always
 * faces you, grows as it comes near and dims as it goes behind). Hovering a disc pauses the orbit,
 * lifts it toward you and opens a card beside it; the page's firefly points at it. Clicking goes
 * to that teammate's roster card. Reduced motion: a still frame, hover still works.
 */
import "./team.css";

const MARKS = {
  Scout:
    '<circle cx="12" cy="12" r="9" /><path d="M15.5 8.5l-2 5-5 2 2-5z" class="fill" />',
  Quill:
    '<path d="M19 4c-6 1-10 5-12 11l-2 5 5-2c6-2 10-6 11-12z" /><path d="M7 17l6-6" />',
  Sketch:
    '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" /><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5" />',
  Pixel:
    '<rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="4" width="7" height="7" rx="1.5" class="fill" /><rect x="4" y="13" width="7" height="7" rx="1.5" class="fill" /><rect x="13" y="13" width="7" height="7" rx="1.5" />',
  Ledger:
    '<rect x="3.5" y="4" width="17" height="16" rx="2" /><path d="M3.5 9.5h17M3.5 15h17M9 4v16M15 4v16" />',
  Atlas:
    '<circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" />',
  Penny:
    '<circle cx="12" cy="12" r="8.5" /><path d="M12 7v10M9.5 9.5c0-1 1-1.5 2.5-1.5s2.5.6 2.5 1.6c0 2.4-5 1.6-5 4.1 0 1 1 1.7 2.5 1.7s2.5-.6 2.5-1.6" />',
  Scribe: '<path d="M4 12h2M8 8v8M12 5v14M16 8v8M20 12h-2" />',
  Sage: '<rect x="5" y="7" width="13" height="13" rx="2" class="fill" /><rect x="7.5" y="4" width="13" height="13" rx="2" />',
  Patch: '<path d="M8 6l-5 6 5 6M16 6l5 6-5 6M13.5 4l-3 16" />',
  Ditto:
    '<rect x="4" y="4" width="11" height="11" rx="2.5" /><rect x="9" y="9" width="11" height="11" rx="2.5" class="fill" />',
  Trace:
    '<circle cx="11" cy="11" r="6.5" /><path d="M16 16l5 5M11 7.5V11l2.5 1.5" />',
  Pyxi: '<g transform="rotate(-30 12 12)"><ellipse cx="12" cy="9" rx="2.3" ry="2.6" /><circle cx="12" cy="5.6" r="1.6" /><ellipse cx="12" cy="14.6" rx="3" ry="3.7" class="fill" /><path d="M9.6 8l-3.8-2.4M14.4 8l3.8-2.4" /></g>',
};

// Names, colours and roles match src/pyxi/agents/registry.py; "reads" matches docs/PRIVACY.md.
const TEAM = [
  {
    name: "Scout",
    c: "#7cc7ff",
    role: "Research and browsing",
    line: "Drives a real browser you can watch. Prefers official sources, saves facts with links, never guesses.",
    reads: "The pages it opens, in a browser on your computer.",
    ask: "Is this cheaper anywhere?",
    ring: 0,
  },
  {
    name: "Quill",
    c: "#f0a9c9",
    role: "Writing",
    line: "Clear, friendly writer. Short sentences, specific details, no fluff.",
    reads: "The emails and notes the errand involves.",
    ask: "Sam emailed my recommendation letter, fix it",
    ring: 0,
  },
  {
    name: "Sketch",
    c: "#86e0c3",
    role: "3D",
    line: "Turns any idea into a tidy 3D scene with a five-step tour, and real things into models you can take apart.",
    reads: "Your question and the screen you're on.",
    ask: "How is an AI chip built? Show me in 3D.",
    ring: 0,
  },
  {
    name: "Pixel",
    c: "#b9a3ff",
    role: "Files",
    line: "Converts, merges, splits and shrinks files in seconds, and never touches the original.",
    reads:
      "The files you hand it, on your computer. Nothing goes to the AI model.",
    ask: "Merge these three PDFs",
    ring: 0,
  },
  {
    name: "Ledger",
    c: "#8fd694",
    role: "Spreadsheets and documents",
    line: "Edits spreadsheets, documents and decks directly, with real formulas, and keeps your original safe.",
    reads: "An outline of the file, to decide the edits.",
    ask: "Add a totals row to this spreadsheet",
    ring: 1,
  },
  {
    name: "Atlas",
    c: "#ffb27a",
    role: "Travel",
    line: "Reads your confirmations, lays the trip out day by day, and reminds you before you need to leave.",
    reads: "Airline and hotel confirmations in your mail, on your computer.",
    ask: "Lay out my trip from my confirmations",
    ring: 1,
  },
  {
    name: "Penny",
    c: "#e8d36a",
    role: "Money",
    line: "Adds up what you really spent from your receipts, and watches your budget and subscriptions.",
    reads:
      "Receipts, billing and order emails, totalled on your computer. No AI model.",
    ask: "What did I spend this month?",
    ring: 1,
  },
  {
    name: "Scribe",
    c: "#a0d8ef",
    role: "Meetings",
    line: "Listens to the whole recording and writes down what was decided and who does what by when.",
    reads:
      "The recording, transcribed on your computer. Only the transcript is sent.",
    ask: "Turn this recording into notes",
    ring: 1,
  },
  {
    name: "Sage",
    c: "#c3e88d",
    role: "Study",
    line: "Turns your slides and notes into flashcards and quizzes, and asks you out loud.",
    reads: "The text of your slides and notes.",
    ask: "Make flashcards from these slides",
    ring: 2,
  },
  {
    name: "Patch",
    c: "#f78c6c",
    role: "Code",
    line: "Reads the error, finds the exact line, and proposes the smallest fix for you to approve.",
    reads: "The error and the lines around it.",
    ask: "Why is this failing?",
    ring: 2,
  },
  {
    name: "Ditto",
    c: "#c792ea",
    role: "Repetitive tasks",
    line: "Watches you do a boring task once, then does the rest the quiet way.",
    reads:
      "The task you show it: windows, fields and small screenshots. Never a password field.",
    ask: "Watch me do this once, then do the rest",
    ring: 2,
  },
  {
    name: "Trace",
    c: "#89ddff",
    role: "Memory",
    line: "Searches your screen history, files and mail at once and points at the exact window.",
    reads: "Your screen history, files and mail, when you ask.",
    ask: "Where did I see that pricing page last week?",
    ring: 2,
  },
];
const PYXI = {
  name: "Pyxi",
  c: "#ffc861",
  role: "The companion",
  line: "Sees your screen, points and draws, explains in 3D, and decides who on the team should take a job.",
  reads:
    "The window you're on, only while you talk to it. Password fields are blacked out first.",
  ask: "Where's the export button?",
};

// Three rings: radius as a share of the stage, tilt out of the screen, turn around it, and speed.
const RINGS = [
  { r: 0.25, tilt: 64, turn: -18, speed: 0.13 },
  { r: 0.355, tilt: 70, turn: 28, speed: -0.09 },
  { r: 0.46, tilt: 58, turn: 72, speed: 0.065 },
];
const D2R = Math.PI / 180;

export function initTeam() {
  if (!document.body.classList.contains("page-team")) return;
  const forceMotion =
    new URLSearchParams(location.search).has("motion") ||
    document.documentElement.classList.contains("force-motion");
  const reduced =
    !forceMotion && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fine = matchMedia("(hover: hover) and (pointer: fine)").matches;
  const orbit = document.querySelector(".orbit");
  if (!orbit) return;
  const sys = orbit.querySelector(".orbit-sys");
  const satsEl = orbit.querySelector(".orbit-sats");
  const card = orbit.querySelector(".orbit-card");
  const core = orbit.querySelector(".orbit-core");
  const paths = [...orbit.querySelectorAll(".orbit-path")];
  const fly = () => window.pyxi?.fly;
  const say = (s) => window.pyxi?.say?.(s);

  // One disc per teammate, a button so it works from the keyboard too.
  const sats = TEAM.map((m, i) => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "sat";
    el.style.setProperty("--c", m.c);
    el.dataset.mate = m.name;
    el.setAttribute("aria-label", `${m.name}, ${m.role}`);
    el.innerHTML = `<span class="sat-disc"><svg viewBox="0 0 24 24" aria-hidden="true">${MARKS[m.name]}</svg></span><span class="sat-name">${m.name}</span>`;
    satsEl.append(el);
    const onRing = TEAM.filter((t) => t.ring === m.ring);
    const phase = (onRing.indexOf(m) / onRing.length) * Math.PI * 2;
    return { m, el, phase, lift: 0, z: 0 };
  });

  // ------------------------------------------------------------------ geometry
  let W = 0;
  const fit = () => {
    W = orbit.clientWidth;
    RINGS.forEach((ring, i) => {
      const p = paths[i];
      const d = ring.r * 2 * W;
      p.style.width = `${d}px`;
      p.style.height = `${d}px`;
      p.style.transform = `translate(-50%, -50%) rotateZ(${ring.turn}deg) rotateX(${ring.tilt}deg)`;
    });
  };
  new ResizeObserver(fit).observe(orbit);
  fit();

  let mx = 0; // the system's tilt toward the mouse, degrees
  let my = 0;
  let tmx = 0;
  let tmy = 0;
  if (fine && !reduced) {
    const hero = orbit.closest("section");
    hero.addEventListener("pointermove", (e) => {
      const r = orbit.getBoundingClientRect();
      tmy = ((e.clientX - (r.left + r.width / 2)) / r.width) * 16;
      tmx = -((e.clientY - (r.top + r.height / 2)) / r.height) * 12;
    });
    hero.addEventListener("pointerleave", () => (tmx = tmy = 0));
  }

  /** Where a point on a ring ends up, after the ring's tilt and turn and the system's tilt. */
  function place(ring, angle, R) {
    let x = Math.cos(angle) * R;
    let y = Math.sin(angle) * R;
    let z = 0;
    // the ring's tilt (around x), then its turn (around z)
    const t = ring.tilt * D2R;
    [y, z] = [
      y * Math.cos(t) - z * Math.sin(t),
      y * Math.sin(t) + z * Math.cos(t),
    ];
    const u = ring.turn * D2R;
    [x, y] = [
      x * Math.cos(u) - y * Math.sin(u),
      x * Math.sin(u) + y * Math.cos(u),
    ];
    // the system's tilt toward the mouse: around y, then around x (same order as the CSS on .orbit-sys)
    const a = my * D2R;
    [x, z] = [
      x * Math.cos(a) + z * Math.sin(a),
      -x * Math.sin(a) + z * Math.cos(a),
    ];
    const b = mx * D2R;
    [y, z] = [
      y * Math.cos(b) - z * Math.sin(b),
      y * Math.sin(b) + z * Math.cos(b),
    ];
    return { x, y, z };
  }

  // ------------------------------------------------------------------ selection
  let picked = null;
  let pickedAt = 0;
  let rerender = () => undefined; // with reduced motion, a pick redraws the still frame
  const show = (m, anchor) => {
    card.innerHTML = `<header><svg class="card-mark" viewBox="0 0 24 24" aria-hidden="true">${MARKS[m.name]}</svg><div><b>${m.name}</b><span>${m.role}</span></div></header><p>${m.line}</p><p class="card-reads"><b>Reads</b> ${m.reads}</p><p class="card-ask">"${m.ask}"</p><a href="#mate-${m.name.toLowerCase()}">Read more</a>`;
    card.style.setProperty("--c", m.c);
    card.classList.add("on");
    orbit.classList.add("picking");
    say(`${m.name}: ${m.role.toLowerCase()}.`);
    const f = fly();
    if (f && anchor) f.point(anchor, m.name, { hold: 6000 });
    placeCard(anchor);
  };
  const hide = () => {
    picked = null;
    card.classList.remove("on");
    orbit.classList.remove("picking");
    sats.forEach((s) => s.el.classList.remove("on"));
    core.classList.remove("on");
    rerender();
  };
  /** The card sits beside its disc, on whichever side has room, never off the stage. */
  function placeCard(anchor) {
    if (!anchor) return;
    const o = orbit.getBoundingClientRect();
    const a = anchor.getBoundingClientRect();
    const ax = a.left + a.width / 2 - o.left;
    const ay = a.top + a.height / 2 - o.top;
    const cw = card.offsetWidth;
    const chh = card.offsetHeight;
    let left = ax + a.width / 2 + 18;
    if (left + cw > o.width - 4) left = ax - a.width / 2 - 18 - cw;
    left = Math.max(4, Math.min(o.width - cw - 4, left));
    const top = Math.max(4, Math.min(o.height - chh - 4, ay - chh / 2));
    card.style.transform = `translate3d(${left.toFixed(0)}px, ${top.toFixed(0)}px, 0)`;
  }
  const pick = (s) => {
    if (picked === s) return;
    hide();
    picked = s;
    pickedAt = performance.now();
    if (s === "core") {
      core.classList.add("on");
      show(PYXI, core);
    } else {
      s.el.classList.add("on");
      show(s.m, s.el);
    }
    rerender();
  };
  sats.forEach((s) => {
    s.el.addEventListener("pointerenter", () => fine && pick(s));
    s.el.addEventListener("focus", () => pick(s));
    s.el.addEventListener("click", (e) => {
      if (!fine && picked !== s) {
        e.preventDefault();
        pick(s);
        return;
      }
      document
        .getElementById(`mate-${s.m.name.toLowerCase()}`)
        ?.scrollIntoView({
          behavior: reduced ? "auto" : "smooth",
          block: "center",
        });
    });
  });
  core.addEventListener("pointerenter", () => fine && pick("core"));
  core.addEventListener("focus", () => pick("core"));
  core.addEventListener("click", (e) => {
    if (!fine && picked !== "core") {
      e.preventDefault();
      pick("core");
      return;
    }
    document.getElementById("mate-pyxi")?.scrollIntoView({
      behavior: reduced ? "auto" : "smooth",
      block: "center",
    });
  });
  orbit.addEventListener("pointerleave", () => fine && hide());
  card.addEventListener(
    "pointerenter",
    () => picked && (pickedAt = performance.now()),
  );
  document.addEventListener("click", (e) => {
    if (!fine && picked && !orbit.contains(e.target)) hide();
  });

  // ------------------------------------------------------------------ the frame
  let spin = 0;
  let speed = 1; // eases to 0 while something is picked
  let last = 0;
  let visible = true;
  new IntersectionObserver(([e]) => (visible = e.isIntersecting)).observe(
    orbit,
  );
  const DEPTH = 0.55; // how much nearer discs grow
  const frame = (time) => {
    if (!reduced) requestAnimationFrame(frame);
    if (!visible || document.hidden) return;
    const dt = Math.min(0.05, (time - last) / 1000 || 0.016);
    last = time;
    const wantSpeed = picked ? 0 : 1;
    speed += (wantSpeed - speed) * 0.08;
    if (!reduced) spin += dt * speed;
    mx += (tmx - mx) * 0.06;
    my += (tmy - my) * 0.06;
    sys.style.transform = `rotateX(${mx.toFixed(2)}deg) rotateY(${my.toFixed(2)}deg)`;
    const Rmax = RINGS[2].r * W;
    for (const s of sats) {
      const ring = RINGS[s.m.ring];
      const R = ring.r * W;
      const angle = s.phase + spin * ring.speed * Math.PI * 2;
      const p = place(ring, angle, R);
      const lift = picked === s ? 1 : 0;
      s.lift += (lift - s.lift) * 0.12;
      const z = p.z + s.lift * 90;
      const depth = (z + Rmax) / (2 * Rmax); // 0 far, 1 near
      const scale = 1 - DEPTH / 2 + depth * DEPTH + s.lift * 0.12;
      s.el.style.transform = `translate3d(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px, ${z.toFixed(1)}px) scale(${scale.toFixed(3)})`;
      s.el.style.setProperty("--depth", depth.toFixed(3));
      s.el.style.zIndex = String(Math.round(1000 + z));
      s.z = z;
    }
    if (picked && picked !== "core") placeCard(picked.el);
    else if (picked === "core") placeCard(core);
  };
  requestAnimationFrame(frame);
  if (reduced) {
    // still: one frame now, on a pick, and whenever the stage changes size
    rerender = () => requestAnimationFrame(frame);
    sats.forEach((s) => (s.lift = 0));
    new ResizeObserver(rerender).observe(orbit);
  }
}

initTeam();
