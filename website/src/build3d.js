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
const RAM_STEPS = [
  { at: 6.6, title: "Open the two clips", say: "The clips at each end of the slot swing open first." },
  { at: 8.0, title: "Line up the notch", say: "The stick only goes in one way: its notch meets the ridge in the slot." },
  { at: 9.4, title: "Press until both clips click", say: "Press down evenly, and the clips close by themselves." },
];
const ASK = {
  ram: "How do I put RAM in my PC?",
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
  let current = "ram";
  const ramFrame = $(".b3-ram");
  let ramAt = 6.0; // where the RAM guide is in its own seconds (it starts as the model builds)
  let ramLast = 0;
  let ramDrawn = 0;
  let ramRaf = 0;
  const ramWindow = () => (ramFrame.contentDocument?.body?.dataset.ready ? ramFrame.contentWindow : null);
  /** The RAM guide is its own page that draws itself from a time: step it here, round and round. */
  const ramTick = (now) => {
    ramRaf = 0;
    if (current !== "ram" || !visible) return;
    const w = ramWindow();
    if (w) {
      if (!ramFrame.dataset.set) {
        ramFrame.dataset.set = "1";
        ramFit();
        w.setup({ c: 999, total: 999, phrases: RAM_STEPS.map((s, i) => ({ text: s.title + ".", at: s.at - 0.1, until: (RAM_STEPS[i + 1]?.at ?? 11.4) - 0.1 })) });
      }
      // the guide is drawn 30 times a second: its moves are slow, and it leaves the page room to scroll smoothly
      if (!reduced && now - ramDrawn < 30) {
        ramRaf = requestAnimationFrame(ramTick);
        return;
      }
      ramDrawn = now;
      ramAt = reduced ? 11.4 : ramAt + Math.min(0.1, (now - (ramLast || now)) / 1000);
      if (ramAt > 13) ramAt = 6.0;
      w.draw(ramAt);
      [...steps.children].forEach((li, k) => {
        li.classList.toggle("on", ramAt >= RAM_STEPS[k].at && (k === 2 || ramAt < RAM_STEPS[k + 1].at));
        li.classList.toggle("done", k < 2 && ramAt >= RAM_STEPS[k + 1].at);
      });
    }
    ramLast = now;
    if (!reduced || !w) ramRaf = requestAnimationFrame(ramTick);
  };
  let ramPixels = 0;
  const ramFit = () => {
    const k = Math.max(stageBox.clientWidth / 1920, stageBox.clientHeight / 1080);
    ramFrame.style.transform = `translate(-50%, -50%) scale(${k.toFixed(4)})`;
    // the 3D picture is worked out at the size it is shown (sharp up to a 1.5x screen), not at full film size
    const px = Math.min(1, Math.max(0.3, k * Math.min(1.5, devicePixelRatio || 1)));
    if (Math.abs(px - ramPixels) > 0.02 && ramWindow()) ramWindow().setup({ pixels: (ramPixels = px) });
  };
  const showRam = () => {
    el.classList.add("is-ram", "is-live");
    el.classList.remove("is-building", "is-ended");
    if (!ramFrame.src) ramFrame.src = ramFrame.dataset.src;
    ramFit();
    stage?.setActive(false);
    steps.replaceChildren(
      ...RAM_STEPS.map((s, i) => {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.innerHTML = `<i>${i + 1}</i><b>${s.title}</b>`;
        btn.addEventListener("click", () => (ramAt = s.at - 0.3));
        const say = document.createElement("p");
        say.className = "b3-say";
        say.innerHTML = `<span>${s.say}</span>`;
        li.append(btn, say);
        return li;
      }),
    );
    ramAt = 6.0;
    ramLast = 0;
    if (!ramRaf) ramRaf = requestAnimationFrame(ramTick);
  };
  new ResizeObserver(ramFit).observe(stageBox);
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
    if (name === "ram") return showRam();
    el.classList.remove("is-ram");
    stage.setActive(visible);
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
    if (current !== "ram") stage.setActive(true);
    else if (!ramRaf) ramRaf = requestAnimationFrame(ramTick);
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
  // the RAM guide can start before the 3D engine for the other two has arrived
  new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && current === "ram" && !started && ((visible = true), (started = true), showRam()), { threshold: 0.12 }).observe(stageBox);

  // fetch the engine a screen before the section arrives; play only while it is on screen
  new IntersectionObserver(
    (entries, o) => {
      if (entries.some((e) => e.isIntersecting)) {
        o.disconnect();
        // the section sits just under the first screen: let that finish loading first
        const idle = () => (window.requestIdleCallback ?? setTimeout)(boot, { timeout: 1200 });
        if (document.readyState === "complete") idle();
        else addEventListener("load", idle, { once: true });
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
