<div align="center">

# Wisp

**A quiet, luminous presence on your desktop that sees your screen, talks with you, points at things, sends agents off to do real work, and explains in 3D.**

Electron · React Three Fiber · Python · LangGraph · Playwright (via [Otto](https://github.com/marzukchowdhury/otto)) · faster-whisper · Kokoro-82M · Claude or a local model

<img src="docs/images/explanation.png" alt="A 3D explanation of HTTPS rising out of a glass surface over a real settings window" width="920">

</div>

---

Wisp is an attempt at a new interface paradigm, not an assistant bolted onto the desktop. A thin glowing diamond lives beside your cursor and a slim glass notch sits on the top edge of your screen. Hold <kbd>Ctrl</kbd>+<kbd>Alt</kbd> and just talk about anything on screen. Wisp looks (screenshot plus the accessibility tree), answers in a natural voice with a sense of humour, and *shows* rather than tells. It writes field notes beside the paragraph it's explaining, rings the button you need, walks you through an app one real click at a time, builds animated 3D models of real things, and runs errands in your connected accounts, asking before anything leaves your PC.

For real work, Wisp delegates to named teammates. Scout drives a real browser, Quill writes, Sketch builds 3D scenes, and they hand work to each other while you watch. Afterwards any browser job can be replayed as a **Ghost Walkthrough** that teaches you how to do it yourself. And because Wisp can keep a **Memory Palace** of what was on your screen (opt-in), you can say *"take me back to the pricing page this morning"* or *"how did I get here?"* and explore the answer in 3D.

## What makes it different

| | |
|---|---|
| **3D explanation layer** | The model writes a typed **Scene spec** (nodes, edges, layouts, narrated steps), never code. A React Three Fiber renderer turns it into dark-glass objects with luminous rims, flowing particles and a camera that tours each step while Wisp narrates. |
| **Exploded views of the real UI** | "What is all this?" peels the actual panel apart: every element is cropped from the screen and placed *exactly* over its real pixels, then lifted into depth by hierarchy. |
| **Memory Palace and time travel** | An opt-in spatial history of your screen (windows, focus changes, element positions). "Take me back to…" rebuilds a past interface as an explorable hologram with a time scrubber. |
| **Causal explanation mode** | "How did I get here?" turns your recent activity into a 3D chain of events with *led to* threads and dashed *what if* branches. |
| **Persistent named agents** | Teammates with personas, voices, their own long-term memory and real capabilities. Handoffs are first-class: Scout researches, then Quill writes from Scout's result. |
| **Ghost Walkthroughs** | Every browser job records what was clicked and why, so it can be replayed as a tutorial with a ghost pointer and spotlight on each element. |
| **3D models of real things** | For "how does a jet engine work?" the model assembles a real-looking object from parts (cylinders, gears, springs, tubes, extrusions) with materials, callouts and narrated beats that animate like a short video. Follow-ups such as "show the inside" or "make it spin" edit the same model. |
| **Field notes on screen** | "Explain this meme / chart / page" writes a notebook card beside the content. Each point writes itself in ink as Wisp says it, frames the exact thing it means with a numbered hand-drawn outline, and a thread links the two. Freehand sketches draw trends on real charts. The card is placed wherever it covers the least text. |
| **Guides that wait for you** | "How do I turn this on?" marks the one control to click, waits for your real click, looks again, and gives the next step. |
| **The notch** | Hold <kbd>Ctrl</kbd>+<kbd>Alt</kbd> to talk; it shows Listening, then "Looking at Chrome", then streams Wisp's words. Hover it for a small chat; double-tap <kbd>Ctrl</kbd> to type. Errors (mic blocked, no key) show up here instead of failing silently. |
| **A voice studio** | Pick who Wisp sounds like: free on-device Kokoro voices, or ElevenLabs and Gemini voices with your own key. Each voice has a drawn signature and an in-character preview; speed, language and captions are one tap away. Sentences can carry a tone that premium voices perform. |
| **Connected accounts** | Gmail, Google Calendar, Google Docs, Discord and LinkedIn. "Sam emailed my recommendation letter, fix it" finds the email, saves the attachment, has Quill fix it into a copy beside the original, and offers to reply with it. Anything that reaches other people waits for an approval card. See [docs/CONNECT.md](docs/CONNECT.md). |
| **Quick actions** | Opens sites and apps, copies exact text, or drops a drafted reply straight into the box you're typing in. |
| **Local-first** | Whisper and Kokoro run on your machine. Local mode uses Ollama, so nothing leaves the computer and it costs $0. |

<p>
<img src="docs/images/field-notes.png" alt="Field notes: a handwritten card beside a settings window, each point framing the exact control it explains" width="56%">
<img src="docs/images/object-model.png" alt="A 3D model of a fighter jet mid-ejection, with callouts on the pilot, seat and rocket motor" width="42%">
</p>
<p>
<img src="docs/images/targeting.png" alt="Wisp pointing at a button: an expanding ring, a luminous thread from the presence indicator, a framed checkbox and a circled tab" width="49%">
<img src="docs/images/exploded.png" alt="An exploded view: the real settings window peeled apart into layered glass panes" width="49%">
</p>
<p>
<img src="docs/images/memory-palace.png" alt="Memory Palace: a past interface reconstructed in 3D with a time scrubber" width="49%">
<img src="docs/images/walkthrough.png" alt="Ghost Walkthrough: the agent's run replayed with a spotlight on the field it typed into" width="49%">
</p>

## Quickstart

Wisp targets **Windows 10/11** (the accessibility layer uses Windows UI Automation). You need Python 3.11+, Node 20+, and Chromium for the browser agents.

```bash
git clone https://github.com/marzukchowdhury/wisp.git
cd wisp
python -m venv .venv
```

Activate it (`.venv\Scripts\activate`), then install Wisp. This also installs Otto, the browser-agent engine:

```bash
pip install -e ".[dev]"
```

```bash
playwright install chromium
```

```bash
wisp setup-voice
```

That downloads Kokoro-82M (about 350 MB) for local speech. Then install and start the desktop app, which starts the brain for you:

```bash
cd desktop && npm install && npm run dev
```

**No API key? It still works.** Without a key Wisp runs in demo mode, with a scripted brain that knows a handful of requests ("How does HTTPS work?", "Explode this panel", "Where's the export button?", "Have Scout fill in the demo form", "Show me how you did it"). Everything downstream (voice, pointing, 3D scenes, agents, walkthroughs) is the real system.

**Best quality:** put `ANTHROPIC_API_KEY=...` in `.env` (Claude Opus 5.5 by default).

**Through OpenRouter:** one key for Claude, Gemini, GPT and more, with exact per-request costs in the panel. In `.env`:

```
OPENROUTER_API_KEY=sk-or-...
WISP_MODEL=anthropic/claude-haiku-5.5
WISP_JOB_MODEL=anthropic/claude-sonnet-5.5
```

Set a credit limit on the key in OpenRouter's dashboard for a hard spending cap.

**Fully local:** pull a vision model and start the brain in local mode.

```bash
ollama pull qwen2.5vl:7b
```

```bash
wisp serve --local
```

`wisp doctor` checks keys, voice models, CUDA and Ollama.

### Using it

| Hotkey | |
|---|---|
| Hold <kbd>Ctrl</kbd>+<kbd>Alt</kbd> | Talk while you hold, send when you let go (left <kbd>Alt</kbd> only, so AltGr typing never triggers it) |
| <kbd>Ctrl</kbd>, <kbd>Ctrl</kbd> | Double-tap to type to Wisp in the notch |
| <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Space</kbd> | Talk, press to start and stop (falls back to another combination if an app already owns it) |
| <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>W</kbd> | Show or hide the panel |
| <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>Backspace</kbd> | Dismiss drawings and scenes |
| <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>P</kbd> | Pause screen sharing |

Things to try: *"Explain this meme"* · *"What's the story in this chart? Draw it."* · *"How does a jet engine work? Show me in 3D."* then *"show me the inside"* · *"Walk me through turning on notifications"* · *"Write a funny reply in this box"* · *"What does this setting do?"* · *"Explode this panel"* · *"How does OAuth work? Show me."* · *"Scout, find the pricing for linear.app, then Quill, write me a two-line summary"* · *"Show me how you did it"* · *"Take me back to what I was doing 20 minutes ago"* · *"How did I get here?"* · *"Make me a teammate called Pixel who compares prices across three shops"*.

<p>
<img src="docs/images/panel-chat.png" alt="Panel: conversation" width="32%">
<img src="docs/images/panel-team.png" alt="Panel: team roster" width="32%">
<img src="docs/images/panel-memory.png" alt="Panel: Memory Palace" width="32%">
</p>
<p>
<img src="docs/images/notch.png" alt="The notch opened on hover: a small chat with a text box, mic and the hold-to-talk hint" width="49%">
<img src="docs/images/voice-studio.png" alt="The voice studio: voice cards with drawn signatures, speed and language" width="24%">
</p>

## Architecture

```mermaid
flowchart LR
  subgraph Desktop["Desktop shell (Electron)"]
    direction TB
    OV["Overlay windows<br/>one per display · transparent · click-through<br/>presence · annotations · 3D layer · HUD"]
    PN["Panel window<br/>chat · team · memory · gallery · settings"]
    MAIN["Main process<br/>hotkeys · cursor feed · click-through toggling<br/>content protection · brain lifecycle"]
  end

  subgraph Brain["Brain (Python · FastAPI · one WebSocket)"]
    direction TB
    GRAPH["Companion turn graph (LangGraph)<br/>perceive → think ⇄ act → finish"]
    TOOLS["Tools: say · point · circle · arrow · explain_in_3d<br/>explode_view · delegate · recall_screen · time_travel<br/>causal_replay · pin_note · start_walkthrough · create_agent"]
    JOBS["Job manager<br/>named agents · handoffs · approvals"]
  end

  subgraph Senses["Senses"]
    SCR["Screen: mss + Windows UI Automation<br/>set-of-marks · password masking · private-app exclusion"]
    EARS["Ears: faster-whisper (local)"]
    VOICE["Voice: Kokoro-82M / Chatterbox / OS"]
  end

  subgraph Memory["Memory (SQLite, local)"]
    SPATIAL["Spatial memory + Memory Palace<br/>snapshots · activity · pins (FTS5)"]
    LTM["Per-agent long-term memory"]
    LOG["Turn log + Otto trajectories"]
  end

  OTTO["Otto browser agent<br/>Playwright · guardrails · budgets"]
  LLM["Claude · GPT-4o · Ollama (local)"]

  OV <-->|WebSocket| GRAPH
  PN <-->|WebSocket| GRAPH
  MAIN --- OV
  GRAPH --> TOOLS
  TOOLS --> JOBS
  JOBS --> OTTO
  GRAPH <--> LLM
  GRAPH --> SCR
  GRAPH --> EARS
  GRAPH --> VOICE
  GRAPH <--> Memory
  OTTO --> LOG
```

### One turn

```mermaid
sequenceDiagram
  autonumber
  participant U as You
  participant O as Overlay
  participant B as Brain
  participant S as Screen
  participant M as Model
  U->>O: Ctrl+Alt+Space
  O->>B: ptt_start
  B->>S: capture now (what you saw as you started speaking)
  O->>B: audio (stops on silence)
  B->>B: faster-whisper → text
  B->>M: persona + conversation + team + memories + screen (text + marked image)
  M-->>B: say("Hit [point:12] Export…") + explain_in_3d(scene)
  B-->>O: scene → 3D layer appears beside its anchor
  loop each sentence
    B->>B: Kokoro synthesises
    B-->>O: say(audio, annotations) → ring + thread land as the words are spoken
  end
  B-->>O: scene_audio(step n) → narrated tour, auto-advances
```

### Key decisions and trade-offs

**Electron over Tauri.** A transparent, always-on-top, click-through overlay that catches the mouse only over interactive bits (`setIgnoreMouseEvents(true, { forward: true })`, toggled on hover) is mature in Electron on Windows. Tauri is lighter, but its transparent click-through story is rougher, and the overlay is the product. Overlay windows are excluded from screen capture (`setContentProtection`), so Wisp never sees its own drawings.

**A Scene spec, not generated Three.js.** Letting the model write WebGL code would be flexible but unsafe (you'd be executing model output) and unreliable. Instead it fills a typed schema: nodes with shapes (box, sphere, server, database, user, cloud, card, screen…), edges (flow, arrow, link, dashed), a layout (flow, tree, orbit, stack, grid, timeline, exploded, free) and narrated steps. Invalid parts are repaired rather than rejected. Layout engines, materials, motion and narration timing live in the renderer, so every scene looks designed even when the model is terse.

**Pixel-exact exploded views.** The camera is placed so the z = 0 plane maps 1:1 onto the overlay's pixels. Exploded pieces start flat on their real screen rectangles and lift by depth, so the real interface appears to come apart.

**Structure plus pixels.** The screenshot tells the model what things look like. The Windows UI Automation tree tells it what they are and *exactly* where. Elements get numbered marks on the screenshot (set-of-marks), and the model points by id, which keeps drawings precise.

**Speech-synced drawing.** The model writes markers like `[point:12]` inside its sentence. Speech is synthesised sentence by sentence (first audio in about half a second) and each drawing fires when its sentence starts playing, so Wisp points as it says "here". This idea comes from [Clicky](https://github.com/farzaa/clicky) (MIT).

**Otto as the hands.** Browser work runs on [Otto](https://github.com/marzukchowdhury/otto), a separate browser-agent engine with its own planning loop, guardrails, budgets and trajectories. Wisp adds personas, memory, handoffs and walkthroughs on top. Otto records the element each action touched, plus a clean screenshot per step, which is what makes Ghost Walkthroughs possible.

**Local voice.** Kokoro-82M runs faster than real time on CPU (about 0.35× real-time factor here), so it's the default. Chatterbox Turbo is an optional GPU provider for zero-shot voice cloning and tags like `[laugh]`. Kokoro doesn't clone, so the two are complementary. StyleTTS2 was considered and skipped: it's research code with fragile dependencies. If a voice can't load, Wisp falls back to the OS voice rather than going silent.

## Design language

*Quiet luxury and precision.* Near-black glass, hairline borders, a single ice-blue accent, Inter with tight tracking, and fast spring motion (about 220 to 550 ms).

- **Presence, not a character.** A thin elongated diamond beside the cursor. Idle is barely there, listening pulses with your voice, thinking shimmers, and acting draws a precise ring.
- **Targeting is light.** A soft expanding ring at the target and a thin luminous thread from the presence, recomputed every frame. Every glow has a hairline dark edge, so it survives on light interfaces as well as dark.
- **Explanations emerge.** The explanation surface is a glass plate whose top dissolves into the desktop. 3D content rises above its edge, a tether of light runs back to the element being explained, and the plate grows out of that element.
- **No emoji, no sketches, no clutter.** One icon set (1.5 px strokes) and precise geometry.

## Safety and privacy

| | |
|---|---|
| **Seeing** | The screen is captured when you talk to Wisp. The Memory Palace is **off by default**; when you turn it on it records window titles, focus changes and element positions (no keystrokes), and screenshots only if you also allow that. |
| **Private apps** | Password managers, banking and credential dialogs (configurable regexes) are never captured or recorded. Password fields are masked in every image and never stored. |
| **Control** | <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>P</kbd> pauses all screen access, with a visible indicator. "Forget everything" wipes the palace. Retention defaults to 72 hours. |
| **Acting** | Wisp itself never clicks or types on your desktop in v1: it points and explains. Browser teammates work in their own browser under Otto's guardrails, with domain rules, no password or card fields, and on-screen approval for anything irreversible. |
| **Content** | Screen text is treated as data. The prompt tells the model to ignore instructions it finds on screen. |
| **Cost** | Per-turn and daily budgets across turns and agent jobs, shown in the panel. Local mode costs nothing. |
| **Storage** | Everything (palace, turns, trajectories, memories) stays in `.wisp/` on your machine. |

## Observability

Every turn is logged with what Wisp saw (the marked screenshot and the accessibility-tree text), the tools it called, what it said, tokens, cost and latency. Agent jobs keep Otto's full trajectories (screenshots, actions, outcomes), which `otto runs report <id>` renders as an HTML replay. `GET /api/turns`, `/api/timeline` and `/api/activity` expose the same data.

## Project layout

```
src/wisp/
  companion/   brain.py (LangGraph turn loop) · tools.py · prompts.py · annotations.py · turns.py
  screen/      context.py (capture, marks, masking) · uia.py (Windows UI Automation)
  scenes/      spec.py (Scene schema + repair) · explode.py · gallery.py
  palace/      activity.py · tracker.py · scenes.py (time travel, causal) · when.py
  agents/      registry.py (named agents) · jobs.py (Otto jobs, handoffs)
  memory/      spatial.py (snapshots, FTS5 recall)
  voice/       stt.py (faster-whisper) · tts.py (Kokoro, Chatterbox, fallback)
  walkthrough.py · demo.py · hub.py · server.py · cli.py
desktop/
  src/main/          Electron: windows, hotkeys, cursor, click-through, brain lifecycle
  src/preload/       the bridge (a small explicit API)
  src/renderer/src/
    overlay/         Presence · Annotations · ScenePanel · Walkthrough · Team HUD · Requests
    scene/           SceneView (R3F) · layout.ts (pure, unit-tested) · themes
    panel/           Chat · Team · Memory · Gallery · Settings
  e2e/smoke.mjs      launches the real Electron app and checks both windows
scripts/             regenerate the README screenshots in headless Chromium
tests/               brain, scenes, screen, voice, palace, agents, server (pytest)
```

## Testing

```bash
pytest
```

48 backend tests, all offline (a fake screen, scripted brains, the OS voice). One drives a real Chromium: Scout fills a three-page form, Quill writes it up from Scout's result, and the Ghost Walkthrough is built with exact click targets. Another plays the whole recommendation-letter errand against fake Google APIs: find the email, save the attachment, fix the Word file with its styles intact, draft a threaded reply, and send only after Allow.

```bash
cd desktop && npm test && npm run typecheck
```

14 renderer tests cover layouts and the 3D motion maths (keyframes, looping machinery, piston and crank staying in step).

`node e2e/smoke.mjs` launches the real Electron app and checks the panel and every overlay come up healthy.

## Limitations and roadmap

- **Windows first.** UI Automation, DPI handling and the overlay are built for Windows. macOS would need an AX-tree reader (and could reuse Clicky's ScreenCaptureKit approach).
- **Light desktop control only.** Wisp can open sites and apps, copy text and paste a draft into the field you're typing in, and it guides you click by click, but it never clicks for you. Agents act in their own browser.
- **Real blur.** Electron can't blur other apps behind a transparent overlay, so glass surfaces use a dense tint. The panel uses Windows acrylic.
- **The taskbar strip.** Windows clamps the overlay to the work area, so Wisp can't draw over the taskbar.
- **Verified live** on Claude Haiku 5.5 through OpenRouter (about $0.002 to $0.004 per turn): field notes on a meme and a chart, two 3D models built from scratch plus a follow-up edit, a full click-by-click guide, inserting a reply, memory, and push-to-talk. **Not yet verified live:** the connectors against real accounts (they're tested end to end against fake Google APIs), ElevenLabs and Gemini voices (no keys here), a local Ollama vision model, and Chatterbox on a GPU.
- **3D models are explanations, not engineering drawings.** A small model builds recognisable objects from primitives; bigger models build richer ones.
- **LinkedIn** only allows apps to post; reading your feed or messages goes through a browser agent you can watch.
- **Next:** streaming speech-to-text, recurring background jobs, and macOS.

## Credits

- [Clicky](https://github.com/farzaa/clicky) by Farza (MIT): the overlay-plus-panel structure and speech-synced pointing.
- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) via [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx).
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
- [React Three Fiber](https://github.com/pmndrs/react-three-fiber) and [drei](https://github.com/pmndrs/drei).

## License

MIT. See [LICENSE](LICENSE).
