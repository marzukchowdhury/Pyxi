/**
 * "Explained in 3D" on the home page: a live stage that plays two of Pyxi's own models the way
 * the app does (see build3d/stage.js). This file is the light part that is always loaded: it
 * waits until the section is near, then fetches the 3D engine and the models, and wires the
 * controls round the stage: which model, the steps of the walkthrough, look inside, the look of
 * it, build it again.
 *
 * No WebGL, or the engine fails to load: the section keeps its recorded picture instead.
 * Reduced motion: the model is simply there, already built; nothing plays until a step is picked.
 */
const ASK = {
  engine: "How does a car engine work?",
  seat: "How does a pilot eject from a jet?",
};

export function initBuild3D({ reduced = false } = {}) {
  const el = document.getElementById("b3");
  if (!el) return;
  const $ = (s) => el.querySelector(s);
  const view = $(".b3-view");
  const stageBox = $(".b3-stage");
  const askText = $(".b3-ask-text");
  const count = $(".b3-count");
  const dots = $(".b3-dots");
  const stepTitle = $(".b3-step");
  const steps = $(".b3-steps");
  const inside = $('[data-act="inside"]');
  const tabs = [...el.querySelectorAll("[data-model]")];
  const looks = [...el.querySelectorAll("[data-look]")];
  const models = {};
  let stage = null;
  let loading = false;
  let current = "engine";
  let visible = false;
  let started = false;
  let typing = 0;

  const type = (text) => {
    clearInterval(typing);
    if (reduced) {
      askText.textContent = text;
      return;
    }
    let i = 0;
    askText.textContent = "";
    typing = setInterval(() => {
      askText.textContent = text.slice(0, ++i);
      if (i >= text.length) clearInterval(typing);
    }, 26);
  };

  /** The walkthrough's steps, as a list you can click and as dots under the model. */
  const listSteps = (beats) => {
    steps.replaceChildren(
      ...beats.map((b, i) => {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        const n = document.createElement("i");
        n.textContent = String(i + 1);
        const title = document.createElement("b");
        title.textContent = b.title;
        btn.append(n, title);
        btn.addEventListener("click", () => stage.setBeat(i));
        const say = document.createElement("p");
        say.className = "b3-say";
        const inner = document.createElement("span");
        // each word is its own span, so the sentence can arrive the way it is spoken
        b.narration.split(/\s+/).forEach((w, k) => {
          const s = document.createElement("span");
          s.style.setProperty("--k", k);
          s.textContent = w + " ";
          inner.append(s);
        });
        say.append(inner);
        li.append(btn, say);
        return li;
      }),
    );
    dots.replaceChildren(
      ...beats.map((b, i) => {
        const d = document.createElement("button");
        d.type = "button";
        d.setAttribute("aria-label", `Step ${i + 1}: ${b.title}`);
        d.addEventListener("click", () => stage.setBeat(i));
        return d;
      }),
    );
  };

  const onBuild = (shown, total) => {
    el.classList.toggle("is-building", shown < total);
    count.textContent =
      shown < total ? `Building it: ${shown} of ${total} parts` : `${total} parts`;
  };
  const onBeat = (i, beat) => {
    el.classList.remove("is-building", "is-ended");
    [...steps.children].forEach((li, k) => {
      li.classList.toggle("on", k === i);
      li.classList.toggle("done", k < i);
      li.firstChild.setAttribute("aria-current", k === i ? "step" : "false");
    });
    [...dots.children].forEach((d, k) => {
      d.classList.toggle("on", k === i);
      d.classList.toggle("done", k < i);
    });
    stepTitle.textContent = beat.title;
    insets(); // the bar's width follows its title, and the section may have just come to rest
    // restart the title's little fade
    stepTitle.style.animation = "none";
    void stepTitle.offsetWidth;
    stepTitle.style.animation = "";
    inside.setAttribute("aria-pressed", String(stage.inside()));
  };

  const show = (name) => {
    current = name;
    tabs.forEach((t) => t.setAttribute("aria-pressed", String(t.dataset.model === name)));
    el.classList.remove("is-ended", "is-turned");
    el.classList.add("is-building");
    type(ASK[name]);
    count.textContent = "";
    listSteps(models[name].beats);
    stage.load(models[name]);
    inside.hidden = !stage.hasCasing();
    inside.setAttribute("aria-pressed", "false");
    view.setAttribute(
      "aria-label",
      `A 3D model: ${models[name].title}. ${models[name].subtitle}. Drag to turn it; the steps beside it walk through how it works.`,
    );
  };

  /** Tags stay clear of the question at the top of the stage and the step bar at the bottom. */
  function insets() {
    const box = stageBox.getBoundingClientRect();
    const ask = $(".b3-ask").getBoundingClientRect();
    const bar = $(".b3-bar").getBoundingClientRect();
    stage?.setInsets(ask.bottom - box.top + 8, box.bottom - bar.top + 8);
  }

  async function boot() {
    if (stage || loading) return;
    loading = true;
    try {
      const [{ createStage }, engine, seat] = await Promise.all([
        import("./build3d/stage.js"),
        import("./build3d/models/engine.json"),
        import("./build3d/models/ejection_seat.json"),
      ]);
      models.engine = engine.default;
      models.seat = seat.default;
      stage = createStage(view, {
        reduced,
        onBuild,
        onBeat,
        onEnd: () => el.classList.add("is-ended"),
        onTurn: () => el.classList.add("is-turned"),
      });
      el.classList.add("is-live");
      insets();
      addEventListener("resize", insets);
      if (visible) start();
    } catch (err) {
      el.classList.add("is-no3d"); // the recorded picture stays
      console.warn("3D stage unavailable:", err);
    }
  }
  function start() {
    stage.setActive(true);
    if (!started) {
      started = true;
      show(current);
    }
  }

  tabs.forEach((t) =>
    t.addEventListener("click", () => stage && t.dataset.model !== current && show(t.dataset.model)),
  );
  looks.forEach((b) =>
    b.addEventListener("click", () => {
      if (!stage) return;
      looks.forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
      stage.setStyle(b.dataset.look);
    }),
  );
  inside.addEventListener("click", () => {
    if (stage) inside.setAttribute("aria-pressed", String(stage.toggleInside()));
  });
  $('[data-act="replay"]').addEventListener("click", () => stage && show(current));

  // fetch the engine a screen before the section arrives; play only while it is on screen
  new IntersectionObserver(
    (entries, o) => {
      if (entries.some((e) => e.isIntersecting)) {
        o.disconnect();
        boot();
      }
    },
    { rootMargin: "900px 0px" },
  ).observe(el);
  new IntersectionObserver(
    (entries) => {
      visible = entries[entries.length - 1].isIntersecting;
      if (!stage) return;
      if (visible) start();
      else stage.setActive(false);
    },
    { threshold: 0.12 },
  ).observe(stageBox);
}
