/**
 * The FAQ as a conversation. The answers live once, in the <details> list, and everything else is
 * built from them: the floating stack of popular questions in the hero, the streamed cue-card
 * answer, the live filter, the deep links and the FAQPage structured data.
 */
import "./faq.css";

if (document.body.classList.contains("page-faq")) init();

function init() {
  const forceMotion = new URLSearchParams(location.search).has("motion");
  const reduced =
    !forceMotion && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];

  const items = $$("details.qa");
  const input = $("#faq-ask");
  const hero = $(".faq-hero");
  const stage = $("#qstage");
  const world = $(".qworld", stage);
  const cardsEl = $("#qcards");
  const answer = $("#qanswer");
  const answerQ = $("#qa-q");
  const stream = $("#qa-stream");
  const meant = $("#qa-meant");
  const countN = $("#faq-n");
  const countLabel = $("#faq-n-label");
  const empty = $("#faq-empty");
  if (reduced) hero.classList.add("still");

  const norm = (s) => s.toLowerCase().normalize("NFKD");
  const data = items.map((d) => ({
    el: d,
    id: d.id,
    q: $("summary", d).textContent.trim(),
    a: $(".qa-body", d).textContent.replace(/\s+/g, " ").trim(),
    related: (d.dataset.related || "").split(/\s+/).filter(Boolean),
    popular: Number(d.dataset.popular || 0),
  }));
  const byId = Object.fromEntries(data.map((x) => [x.id, x]));

  // ------------------------------------------------------------ structured data
  const ld = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: data.map((x) => ({
      "@type": "Question",
      name: x.q,
      acceptedAnswer: { "@type": "Answer", text: x.a },
    })),
  };
  const script = document.createElement("script");
  script.type = "application/ld+json";
  script.textContent = JSON.stringify(ld);
  document.head.append(script);

  // ------------------------------------------------------------ the floating stack
  const popular = data
    .filter((x) => x.popular)
    .sort((a, b) => a.popular - b.popular);
  // Where each card rests when it isn't the active one: a loose cloud, in 3D (percent, percent, px).
  const SLOTS = [
    [2, 3, -200],
    [38, 0, -300],
    [66, 6, -160],
    [0, 40, -320],
    [68, 40, -260],
    [4, 82, -180],
    [44, 90, -240],
    [66, 74, -340],
  ];
  const cards = popular.map((x, i) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "qcard";
    b.textContent = x.q;
    b.dataset.id = x.id;
    b.style.setProperty("--sx", `${SLOTS[i % SLOTS.length][0]}%`);
    b.style.setProperty("--sy", `${SLOTS[i % SLOTS.length][1]}%`);
    b.style.setProperty("--sz", `${SLOTS[i % SLOTS.length][2]}px`);
    b.style.setProperty("--d", `${i * 0.7}s`);
    li.append(b);
    cardsEl.append(li);
    b.addEventListener("click", () => {
      userTouched();
      activate(x.id, true);
    });
    return b;
  });

  let active = null;
  let streamTimer = 0;
  const fly = () => window.pyxi?.fly;
  /** Bring a question to the front and let Pyxi answer it beside the card. */
  function activate(id, point) {
    const x = byId[id];
    if (!x) return;
    active = id;
    cards.forEach((c) => c.classList.toggle("on", c.dataset.id === id));
    answerQ.textContent = x.q;
    // Stream the answer word by word, the way the notch speaks.
    clearTimeout(streamTimer);
    stream.textContent = "";
    const words = x.a.split(" ");
    words.forEach((word, i) => {
      const w = document.createElement("span");
      w.className = "w";
      w.textContent = word;
      w.style.animationDelay = reduced ? "0s" : `${Math.min(i * 34, 2600)}ms`;
      stream.append(w, " ");
    });
    // Was this what you meant? Related questions, and the full answer below.
    meant.textContent = "";
    const lead = document.createElement("span");
    lead.textContent = "Was this what you meant? ";
    meant.append(lead);
    x.related
      .map((r) => byId[r])
      .filter(Boolean)
      .forEach((r, i) => {
        if (i) meant.append(" · ");
        const a = document.createElement("a");
        a.href = `#${r.id}`;
        a.textContent = r.q;
        a.addEventListener("click", (e) => {
          e.preventDefault();
          userTouched();
          if (cards.some((c) => c.dataset.id === r.id)) activate(r.id, true);
          else open(r.id, true);
        });
        meant.append(a);
      });
    meant.append(" · ");
    const full = document.createElement("a");
    full.href = `#${x.id}`;
    full.className = "qa-full";
    full.textContent = "Full answer";
    full.addEventListener("click", (e) => {
      e.preventDefault();
      open(x.id, true);
    });
    meant.append(full);
    answer.classList.remove("show");
    void answer.offsetWidth;
    answer.classList.add("show");
    if (point) {
      const card = cards.find((c) => c.dataset.id === id);
      if (card)
        setTimeout(() => fly()?.point(card, "Pyxi", { hold: 2600 }), 350);
    }
  }

  // Until you touch anything, Pyxi works through the popular questions by itself.
  let touched = false;
  let heroOn = true;
  new IntersectionObserver(([e]) => (heroOn = e.isIntersecting), {
    threshold: 0.3,
  }).observe(stage);
  function userTouched() {
    touched = true;
    stage.classList.add("held");
  }
  if (popular.length) {
    let i = 0;
    setTimeout(() => activate(popular[0].id, !reduced), 900);
    if (!reduced) {
      setInterval(() => {
        if (touched || !heroOn || document.hidden) return;
        i = (i + 1) % popular.length;
        activate(popular[i].id, true);
      }, 6500);
    }
  }
  // The stack tilts a little with the mouse, like cards hanging in the air.
  if (!reduced && matchMedia("(hover: hover) and (pointer: fine)").matches) {
    stage.addEventListener("pointermove", (e) => {
      const r = stage.getBoundingClientRect();
      const nx = (e.clientX - r.left) / r.width - 0.5;
      const ny = (e.clientY - r.top) / r.height - 0.5;
      world.style.setProperty("--ry", `${(nx * 10).toFixed(2)}deg`);
      world.style.setProperty("--rx", `${(-ny * 7).toFixed(2)}deg`);
    });
    stage.addEventListener("pointerleave", () => {
      world.style.setProperty("--ry", "0deg");
      world.style.setProperty("--rx", "0deg");
    });
  }

  // ------------------------------------------------------------ the live filter
  const groups = $$(".faq-group");
  function filter() {
    const q = norm(input.value.trim());
    let shown = 0;
    let firstHit = null;
    for (const x of data) {
      const hit = !q || norm(x.q + " " + x.a).includes(q);
      x.el.hidden = !hit;
      if (hit) {
        shown++;
        firstHit ||= x;
      }
    }
    groups.forEach(
      (g) => (g.hidden = !$$("details.qa:not([hidden])", g).length),
    );
    empty.hidden = shown > 0;
    countN.textContent = String(shown);
    countLabel.textContent = shown === 1 ? "answer" : "answers";
    // The floating cards: matching ones come forward, the rest drift back.
    cards.forEach((c) => {
      const x = byId[c.dataset.id];
      const hit = !q || norm(x.q + " " + x.a).includes(q);
      c.classList.toggle("miss", !hit);
    });
    stage.classList.toggle("filtering", !!q);
    const front = q && cards.find((c) => !c.classList.contains("miss"));
    if (front && front.dataset.id !== active) activate(front.dataset.id, false);
    return firstHit;
  }
  input.addEventListener("input", () => {
    userTouched();
    filter();
  });
  input.addEventListener("focus", userTouched);
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const front = cards.find((c) => !c.classList.contains("miss"));
    const q = norm(input.value.trim());
    if (q && front) activate(front.dataset.id, true);
    else {
      const first = data.find((x) => !x.el.hidden);
      if (first) open(first.id, true);
    }
  });
  filter();

  // ------------------------------------------------------------ deep links
  /** Open one answer in the list and bring it into view. */
  function open(id, scroll) {
    const x = byId[id];
    if (!x) return;
    x.el.open = true;
    history.replaceState(null, "", `#${id}`);
    if (scroll)
      x.el.scrollIntoView({
        behavior: reduced ? "auto" : "smooth",
        block: "center",
      });
    x.el.classList.remove("lit");
    void x.el.offsetWidth;
    x.el.classList.add("lit");
  }
  const fromHash = () => {
    const id = location.hash.slice(1);
    if (byId[id]) open(id, true);
  };
  addEventListener("hashchange", fromHash);
  fromHash();
  // Opening an answer in the list puts its id in the address bar, so it can be shared.
  items.forEach((d) =>
    d.addEventListener("toggle", () => {
      if (d.open) history.replaceState(null, "", `#${d.id}`);
    }),
  );
}
