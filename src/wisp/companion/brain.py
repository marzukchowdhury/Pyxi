"""Wisp's brain: one LangGraph run per conversation turn.

    START -> perceive -> think -> act --+--> finish -> END
                           ^            |
                           +------------+   (an info tool like recall_screen / look_closer
                                             asked for another round, budget permitting)

* **perceive** grabs the screen (or uses the capture taken the moment push-to-talk began, so
  Wisp sees what you saw when you started speaking), records it in spatial memory and
  renders a set-of-marks image.
* **think** is one model call with the persona, conversation, team status, memories, the
  screen (text + image) and any lookup results.
* **act** runs tools. Drawings and 3D scenes go to the overlay immediately; speech streams
  sentence by sentence with inline markers synced to the audio; jobs start in the background.
* **finish** makes sure something was said, logs the turn and its cost.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import secrets
import time
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from otto.llm.base import ImagePart, LLMProvider, Message, TextPart
from otto.storage import MemoryStore, TrajectoryStore
from otto.types import Usage
from PIL import Image
from pydantic import ValidationError

from wisp.agents import AgentProfile, AgentRegistry, Job, JobManager
from wisp.agents.jobs import Handoff
from wisp.companion import prompts
from wisp.companion.annotations import (
    Annotation,
    from_marker,
    resolve_point,
    resolve_rect,
    split_markers,
)
from wisp.companion.notes import Guide, build_card
from wisp.companion.tools import COMPANION_TOOLS, INFO_TOOLS
from wisp.companion.turns import TurnStore
from wisp.config import WispSettings
from wisp.hub import Hub
from wisp.memory import SpatialMemory
from wisp.palace import ActivityLog, causal_scene, parse_when, snapshot_scene, strip_when
from wisp.scenes import SceneSpec, build_exploded_scene, get_scene, parse_scene
from wisp.scenes.object_gallery import OBJECT_GALLERY
from wisp.scenes.objects import ObjectEdit, ObjectScene, apply_edit, object_summary
from wisp.scenes.spec import SceneAnchor
from wisp.screen import ScreenContext, ScreenReader
from wisp.voice import Transcriber, make_tts, split_sentences
from wisp.voice.studio import LANGUAGES, VoiceStudio
from wisp.voice.tts import FallbackTTS
from wisp.walkthrough import build_walkthrough

log = logging.getLogger(__name__)
State = Literal["idle", "listening", "transcribing", "looking", "thinking", "speaking"]


class TurnState(TypedDict, total=False):
    user_text: str
    turn_id: str
    round: int
    notes: list[str]
    needs_more: bool
    stop: str | None


class Brain:
    def __init__(
        self,
        settings: WispSettings,
        hub: Hub,
        *,
        llm: LLMProvider | None = None,
        otto_llm: LLMProvider | None = None,
        tts: FallbackTTS | None = None,
        screen: ScreenReader | None = None,
    ) -> None:
        from wisp.llm import make_llm

        self.settings = settings
        self.hub = hub
        self.llm = llm or make_llm(settings)
        self.tts = tts or make_tts(
            settings.voice, settings.models_dir, settings.data_dir / "voice.json"
        )
        self.stt = Transcriber(settings.voice)
        self.screen = screen or ScreenReader(settings.screen)
        db = settings.db_path
        self.registry = AgentRegistry(db)
        thumbs = settings.palace_dir if settings.screen.keep_screenshots else None
        self.spatial = SpatialMemory(
            db, retention_hours=settings.screen.retention_hours, thumbs_dir=thumbs
        )
        self.activity = ActivityLog(db)
        self.memory = MemoryStore(db)
        self.turns = TurnStore(db, settings.turns_dir)
        otto_dir = settings.data_dir / "otto"
        self.otto_store = TrajectoryStore(otto_dir / "otto.db", otto_dir / "runs")
        self.jobs = JobManager(
            settings,
            self.registry,
            hub,
            llm_factory=lambda agent: self.llm,
            scene_maker=self._scene_for_job,
            otto_llm=otto_llm,
            on_done=self._job_done,
        )
        self.history: list[tuple[str, str]] = []
        self.state: State = "idle"
        self._lock = asyncio.Lock()
        self._prefetch: asyncio.Task[ScreenContext] | None = None
        self._speech: asyncio.Task[None] | None = None
        self._speech_gen = 0
        self._bg: set[asyncio.Task[Any]] = set()
        self._graph = self._build_graph()
        # per-turn scratch
        self._ctx: ScreenContext | None = None
        self._images: list[bytes] = []
        self._usage = Usage()
        self._said: list[str] = []
        self._calls: list[dict[str, Any]] = []
        self._scene_retry = False
        self._object: ObjectScene | None = None  # the 3D model on screen, so follow-ups can edit it
        self._guide: Guide | None = None  # a click Wisp is waiting for

    # ================================================================== entry points
    async def set_state(self, state: State) -> None:
        self.state = state
        await self.hub.emit("state", state=state)

    def begin_listening(self) -> None:
        """Push-to-talk pressed: capture the screen *now*, while the user starts speaking."""
        self.interrupt()
        self._prefetch = asyncio.create_task(self.screen.capture())
        self._spawn(self.set_state("listening"))

    async def hear(self, audio: bytes) -> None:
        await self.set_state("transcribing")
        try:
            studio = self.studio
            text, took, spoken = await self.stt.transcribe(
                audio, studio.prefs.language if studio else "en"
            )
            if studio:
                studio.detected_language = spoken
        except Exception as exc:
            log.exception("transcription failed")
            await self.hub.emit("error", message=f"I couldn't hear that: {exc}")
            await self.set_state("idle")
            return
        await self.hub.emit("transcript", text=text, seconds=round(took, 2))
        if not text.strip():
            await self.say_line("Sorry, I didn't catch that.")
            await self.set_state("idle")
            return
        await self.turn(text)

    async def guide_clicked(self) -> None:
        """The user clicked the control Wisp was waiting on: look again and give the next step."""
        guide = self._guide
        if guide is None or self._lock.locked():
            return
        await asyncio.sleep(0.7)  # let the app react to the click before looking
        await self.turn(
            f"(I clicked '{guide.label}', step {guide.step}. Look at the screen now and guide me to the next "
            f"step toward: {guide.goal}. If that's done, tell me and call guide with done=true.)",
            shown=f"Clicked {guide.label}",
        )

    def interrupt(self) -> None:
        self._speech_gen += 1
        self._spawn(self.hub.emit("stop_audio"))

    async def turn(self, user_text: str, *, shown: str | None = None) -> None:
        async with self._lock:
            self._ctx, self._images, self._usage, self._said, self._calls = (
                None,
                [],
                Usage(),
                [],
                [],
            )
            self._scene_retry = False
            turn_id = "turn_" + secrets.token_hex(4)
            started = time.monotonic()
            await self.hub.emit("user", text=shown or user_text, turn_id=turn_id)
            error = None
            try:
                await self._graph.ainvoke(
                    {
                        "user_text": user_text,
                        "turn_id": turn_id,
                        "round": 0,
                        "notes": [],
                        "needs_more": False,
                        "stop": None,
                    }
                )
            except Exception as exc:
                log.exception("turn failed")
                error = f"{type(exc).__name__}: {exc}"
                await self.say_line("Oops, my brain hiccupped. Mind trying that again?")
            said = " ".join(self._said)
            self.history = [*self.history[-11:], (user_text, said)]
            ctx = self._ctx
            image_path = None
            if ctx and self._images:
                image_path = self.turns.save_image(turn_id, self._images[0])
            self.turns.record(
                {
                    "id": turn_id,
                    "ts": time.time(),
                    "user_text": user_text,
                    "app": ctx.window.app if ctx else None,
                    "window": ctx.window.title if ctx else None,
                    "screen_text": ctx.render(60) if ctx else None,
                    "image_path": image_path,
                    "tool_calls": self._calls,
                    "said": said,
                    "rounds": len({c["round"] for c in self._calls}),
                    "input_tokens": self._usage.input_tokens + self._usage.cache_read_tokens,
                    "output_tokens": self._usage.output_tokens,
                    "cost_usd": self._usage.cost_usd,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "error": error,
                }
            )
            if self._speech:
                await asyncio.gather(self._speech, return_exceptions=True)
            await self.hub.emit(
                "turn_done",
                turn_id=turn_id,
                cost_usd=round(self._usage.cost_usd, 5),
                tokens=self._usage.total_tokens,
                spent_today=round(self.turns.spent_today(), 4),
                daily_budget=self.settings.budget.daily_cost_usd,
            )
            await self.set_state("idle")

    # ================================================================== graph
    def _build_graph(self) -> Any:
        async def perceive(state: TurnState) -> TurnState:
            await self.set_state("looking")
            ctx = await self._prefetch if self._prefetch else await self.screen.capture()
            self._prefetch = None
            self._ctx = ctx
            await self.hub.emit(
                "capture",
                app=ctx.window.app,
                window=ctx.window.title[:120],
                excluded=ctx.excluded,
                elements=len(ctx.elements),
            )
            if not ctx.excluded:
                sid = self.spatial.record(ctx, force=True)
                self.activity.log(
                    "turn",
                    app=ctx.window.app,
                    title=ctx.window.title,
                    snapshot_id=sid,
                    detail=state.get("user_text", ""),
                )
                if image := ctx.model_image(self.settings.screen.model_image_width):
                    self._images = [image]
            return {}

        async def think(state: TurnState) -> TurnState:
            cfg = state
            budget = self.settings.budget
            if self.turns.spent_today() >= budget.daily_cost_usd:
                await self.say_line(
                    "I've used up today's budget, so I'm taking a breather. You can raise it in settings."
                )
                return {"stop": "daily budget", "needs_more": False}
            await self.set_state("thinking")
            wisp = self.registry.get("wisp")
            system = prompts.system_prompt(
                wisp.persona if wisp else "", self.registry.roster_text()
            )
            jobs = "\n".join(j.brief() for j in self.jobs.recent(6))
            memories = "\n".join(
                m.render() for m in self.memory.search("agent:wisp", cfg["user_text"], 6)
            )
            text = prompts.turn_prompt(
                user_text=cfg["user_text"],
                history=self.history,
                screen=self._ctx.render(self.settings.screen.max_elements)
                if self._ctx
                else "(no screen)",
                jobs=jobs,
                memories=memories,
                notes=state.get("notes", []),
                on_screen=object_summary(self._object) if self._object else "",
                language=self._reply_language(),
                budget_line=f"spent today ${self.turns.spent_today():.3f} of ${budget.daily_cost_usd:.2f}; this turn ${self._usage.cost_usd:.3f} of ${budget.max_cost_usd:.2f}",
            )
            parts: list[TextPart | ImagePart] = [TextPart(text)] + [
                ImagePart(img, "image/jpeg") for img in self._images
            ]
            response = await self.llm.complete(
                system=system,
                messages=[Message("user", parts)],
                tools=COMPANION_TOOLS,
                max_tokens=9000,  # a detailed 3D model is a long tool call
            )
            self._usage = self._usage + response.usage
            round_no = state.get("round", 0) + 1
            if not response.tool_calls:
                if response.text:
                    self._speak(response.text)
                return {"round": round_no, "needs_more": False}
            notes: list[str] = []
            needs_more = False
            for call in response.tool_calls:
                self._calls.append(
                    {"round": round_no, "tool": call.name, "arguments": call.arguments}
                )
                try:
                    note = await self._act(call.name, dict(call.arguments))
                except Exception as exc:
                    log.exception("tool %s failed", call.name)
                    note = f"{call.name} failed: {exc}"
                if note:
                    notes.append(note)
                if call.name in INFO_TOOLS or (
                    note and note.startswith(("SCENE_INVALID", "MODEL_INVALID"))
                ):
                    needs_more = True
            return {
                "round": round_no,
                "notes": [*state.get("notes", []), *notes],
                "needs_more": needs_more,
            }

        def route(state: TurnState) -> str:
            b = self.settings.budget
            if (
                state.get("needs_more")
                and state.get("round", 0) < b.max_tool_rounds
                and self._usage.cost_usd < b.max_cost_usd
            ):
                return "think"
            return "finish"

        async def finish(state: TurnState) -> TurnState:
            if not self._said and not state.get("stop"):
                self._speak("Done!" if self._calls else "Hmm, I'm not sure what to say to that.")
            return {}

        g = StateGraph(TurnState)
        g.add_node("perceive", perceive)
        g.add_node("think", think)
        g.add_node("finish", finish)
        g.add_edge(START, "perceive")
        g.add_edge("perceive", "think")
        g.add_conditional_edges("think", route, {"think": "think", "finish": "finish"})
        g.add_edge("finish", END)
        return g.compile()

    # ================================================================== tools
    async def _act(self, name: str, args: dict[str, Any]) -> str | None:
        ctx = self._ctx
        if name == "say":
            self._speak(str(args.get("text", "")))
            return None
        if name in {"point", "circle", "highlight"}:
            if name == "point":
                at = resolve_point(
                    ctx,
                    args.get("element_id"),
                    [args["x"], args["y"]] if "x" in args and "y" in args else None,
                )
                rect = resolve_rect(ctx, args.get("element_id"), None)
                ann = (
                    Annotation(kind="point", at=at, rect=rect, label=args.get("label"))
                    if at
                    else None
                )
            else:
                rect = resolve_rect(ctx, args.get("element_id"), args.get("box"))
                ann = Annotation(kind=name, rect=rect, label=args.get("label")) if rect else None
            if ann is None:
                return f"{name}: couldn't find that on screen."
            await self.hub.emit("annotate", annotations=[ann.model_dump()])
            return None
        if name == "arrow":
            a = resolve_point(ctx, args.get("from_element_id"), args.get("from_xy"))
            b = resolve_point(ctx, args.get("to_element_id"), args.get("to_xy"))
            if not (a and b):
                return "arrow: couldn't resolve both ends."
            await self.hub.emit(
                "annotate",
                annotations=[
                    Annotation(kind="arrow", at=a, to=b, label=args.get("label")).model_dump()
                ],
            )
            return None
        if name == "clear_drawings":
            await self.hub.emit("clear")
            return None
        if name == "explain_on_screen":
            card = build_card(ctx, args)
            if not card.items:
                return "explain_on_screen: give at least one point with text."
            await self.hub.emit("notes", card=card.model_dump(mode="json"))
            missing = [it.n for it in card.items if it.mark != "none" and it.rect is None]
            return f"explain_on_screen: points {missing} had nothing to frame." if missing else None
        if name == "guide":
            if args.get("done"):
                self._guide = None
                await self.hub.emit("guide", active=False)
                return None
            rect = resolve_rect(ctx, args.get("element_id"), args.get("box"))
            if rect is None:
                return "guide: couldn't find that on screen; point at it instead."
            label = str(
                args.get("label")
                or (
                    ctx.element(int(args["element_id"])).name
                    if ctx
                    and args.get("element_id") is not None
                    and ctx.element(int(args["element_id"]))
                    else "here"
                )
            )[:40]
            step = (
                self._guide.step + 1 if self._guide and self._guide.goal == args.get("goal") else 1
            )
            self._guide = Guide(goal=str(args["goal"])[:200], label=label, rect=rect, step=step)
            ann = Annotation(kind="click", rect=rect, label=label, ttl_s=600, guide=True)
            await self.hub.emit("annotate", annotations=[ann.model_dump()])
            await self.hub.emit("guide", active=True, step=step, goal=self._guide.goal, label=label)
            return None
        if name == "explain_in_3d":
            try:
                scene = parse_scene(args)
            except ValidationError as exc:
                if self._scene_retry:
                    return "explain_in_3d failed twice; explain in words instead."
                self._scene_retry = True
                return f"SCENE_INVALID: fix these and call explain_in_3d again: {exc.errors()[:6]}"
            if scene.anchor and scene.anchor.element_id is not None:
                rect = resolve_rect(ctx, scene.anchor.element_id, None)
                scene.anchor = SceneAnchor(
                    element_id=scene.anchor.element_id, rect=list(rect) if rect else None
                )
            await self.show_scene(scene)
            return f"Showing 3D scene '{scene.title}' ({len(scene.nodes)} nodes, {len(scene.steps)} steps)."
        if name == "build_3d_model":
            try:
                model = ObjectScene.model_validate(args)
            except ValidationError as exc:
                if self._scene_retry:
                    return "build_3d_model failed twice; explain in words instead."
                self._scene_retry = True
                return f"MODEL_INVALID: fix these and call build_3d_model again: {exc.errors()[:6]}"
            await self.show_object(model, restart=True)
            note = f"Showing 3D model '{model.title}' ({len(model.parts)} parts, {len(model.beats)} beats)."
            return f"{note} Repairs: {'; '.join(model.notes[:6])}" if model.notes else None
        if name == "edit_3d_model":
            if self._object is None:
                return (
                    "edit_3d_model: there's no 3D model on screen; build one with build_3d_model."
                )
            try:
                edit = ObjectEdit.model_validate(args)
                model = apply_edit(self._object, edit)
            except ValidationError as exc:
                return f"MODEL_INVALID: fix these and call edit_3d_model again: {exc.errors()[:6]}"
            await self.show_object(model, restart=edit.beats is not None)
            return None
        if name == "explode_view":
            if ctx is None or ctx.screenshot is None:
                return "explode_view: I don't have a screenshot."
            scene = build_exploded_scene(
                ctx,
                int(args["element_id"]),
                self.settings.crops_dir,
                title=args.get("title"),
                steps=args.get("steps"),
            )
            await self.show_scene(scene)
            return f"Exploded view of [{args['element_id']}] with {len(scene.nodes)} pieces."
        if name == "show_scene":
            key = str(args["name"])
            if key in OBJECT_GALLERY:
                await self.show_object(OBJECT_GALLERY[key](), restart=True)
            else:
                await self.show_scene(get_scene(key))
            return None
        if name == "delegate":
            then = (
                Handoff(agent=args["then_agent"], task=args.get("then_task") or "Use the result.")
                if args.get("then_agent")
                else None
            )
            job = await self.jobs.start(
                str(args["agent"]), str(args["task"]), start_url=args.get("start_url"), then=then
            )
            return f"Started {job.id}: {job.agent_name} is on it."
        if name == "recall_screen":
            hits = self.spatial.recall(str(args["query"]), 5)
            if not hits:
                return f"recall_screen({args['query']!r}): nothing in spatial memory."
            lines = [h.describe() for h in hits]
            if ctx:
                for h in hits:
                    now = next(
                        (e for e in ctx.elements if e.name == h.name and e.role == h.role), None
                    )
                    if now and ctx.window.app.lower() == h.app.lower():
                        lines.append(f'"{h.name}" is on screen right now as [{now.id}].')
                        break
            return "recall_screen results:\n" + "\n".join(lines)
        if name == "look_closer":
            rect = resolve_rect(ctx, args.get("element_id"), args.get("box"))
            crop = ctx.crop(rect, pad=8) if ctx and rect else None
            if crop is None:
                return "look_closer: nothing to zoom into."
            scale = min(3.0, 1100 / max(crop.width, 1))
            crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            crop.convert("RGB").save(buf, format="JPEG", quality=85)
            self._images.append(buf.getvalue())
            return f"Zoomed image of {rect} added below the screenshot."
        if name == "remember":
            self.memory.remember("agent:wisp", str(args["key"]), str(args["content"]))
            return None
        if name == "start_walkthrough":
            await self.start_walkthrough(args.get("job_id"))
            return None
        if name == "get_activity":
            events = self.activity.recent(float(args.get("minutes") or 30))
            if not events:
                return "get_activity: no activity recorded yet (the Memory Palace builds up as the user works)."
            return "Recent activity (oldest first):\n" + "\n".join(
                e.describe() for e in events[-40:]
            )
        if name == "time_travel":
            return await self.time_travel(str(args.get("query", "")), str(args.get("when") or ""))
        if name == "causal_replay":
            ids = [int(i) for i in args.get("event_ids", [])][:10]
            events = sorted(self.activity.get(ids), key=lambda e: e.ts)
            if not events:
                return "causal_replay: none of those event ids exist; call get_activity first."
            snaps = {
                e.snapshot_id: snap
                for e in events
                if e.snapshot_id and (snap := self.spatial.snapshot(e.snapshot_id))
            }
            scene = causal_scene(
                events,
                args.get("links") or [],
                title=str(args.get("title", "How you got here")),
                summary=str(args.get("summary", "")),
                what_ifs=args.get("what_ifs"),
                crops_dir=self.settings.crops_dir,
                snapshots=snaps,
            )
            await self.show_scene(scene)
            return f"Showing the causal chain of {len(events)} events."
        if name == "pin_note":
            e = ctx.element(int(args["element_id"])) if ctx else None
            if e is None:
                return "pin_note: that element isn't on screen."
            self.activity.add_pin(ctx.window.app, e.name, e.role, str(args["note"]))
            await self.emit_pins(ctx.window.app, ctx)
            return None
        if name == "create_agent":
            extra = {"voice": args["voice"]} if args.get("voice") else {}
            agent = self.registry.create(
                str(args["name"]), args["role"], str(args["persona"]), **extra
            )
            await self.hub.emit("agents", agents=[a.model_dump() for a in self.registry.all()])
            return f"Created {agent.name} ({agent.role})."
        return f"Unknown tool {name}."

    # ================================================================== speech
    def _speak(self, text: str, *, agent: str = "Wisp", voice: str | None = None) -> None:
        """Queue speech behind anything already being said."""
        if not text.strip():
            return
        self._said.append(split_markers(text)[0])
        previous, gen, ctx = self._speech, self._speech_gen, self._ctx

        async def run() -> None:
            if previous:
                await asyncio.gather(previous, return_exceptions=True)
            await self._stream_speech(text, agent=agent, voice=voice, gen=gen, ctx=ctx)

        self._speech = asyncio.create_task(run())

    async def say_line(self, text: str, *, agent: str = "Wisp", voice: str | None = None) -> None:
        self._speak(text, agent=agent, voice=voice)
        if self._speech:
            await asyncio.gather(self._speech, return_exceptions=True)

    async def _stream_speech(
        self, text: str, *, agent: str, voice: str | None, gen: int, ctx: ScreenContext | None
    ) -> None:
        sentences: list[str] = []
        for chunk in split_sentences(text):  # a marker alone belongs with the words before it
            if sentences and not split_markers(chunk)[0]:
                sentences[-1] += " " + chunk
            else:
                sentences.append(chunk)
        for i, sentence in enumerate(sentences):
            if gen != self._speech_gen:
                return
            clean, markers = split_markers(sentence)
            anns = [a.model_dump() for kind, eid in markers if (a := from_marker(ctx, kind, eid))]
            notes = [eid for kind, eid in markers if kind.lower() == "note"]
            if not clean and not anns and not notes:
                continue
            speech = await self.tts.synthesize(clean, voice)
            if gen != self._speech_gen:
                return
            if i == 0:
                await self.set_state("speaking")
            await self.hub.emit(
                "say",
                agent=agent,
                index=i,
                total=len(sentences),
                text=speech.text,
                audio=base64.b64encode(speech.wav).decode() if speech.wav else None,
                duration_s=round(speech.duration_s, 2),
                annotations=anns,
                notes=notes,
            )

    # ================================================================== scenes & walkthroughs
    async def show_scene(self, scene: SceneSpec) -> None:
        await self.hub.emit("scene", scene=scene.model_dump(by_alias=True, mode="json"))
        self._spawn(self._narrate(scene))

    async def show_object(self, model: ObjectScene, *, restart: bool) -> None:
        """Put a 3D model on screen. Edits keep the id (the view keeps its camera); new beats get
        a new audio key so old narration can't play over the new explanation."""
        self._object = model
        audio_key = (
            f"{model.id}:{secrets.token_hex(3)}"
            if restart
            else getattr(self, "_object_audio", model.id)
        )
        self._object_audio = audio_key
        await self.hub.emit(
            "object", scene=model.model_dump(mode="json"), audio_key=audio_key, restart=restart
        )
        if restart:
            self._spawn(self._narrate_beats(audio_key, [b.narration for b in model.beats]))

    async def _narrate_beats(self, key: str, lines: list[str]) -> None:
        for i, line in enumerate(lines):
            if not line:
                continue
            speech = await self.tts.synthesize(line)
            await self.hub.emit(
                "scene_audio",
                scene_id=key,
                step=i,
                text=speech.text,
                audio=base64.b64encode(speech.wav).decode() if speech.wav else None,
                duration_s=round(speech.duration_s, 2),
            )

    async def _narrate(self, scene: SceneSpec) -> None:
        for i, step in enumerate(scene.steps):
            if not step.narration:
                continue
            speech = await self.tts.synthesize(step.narration)
            await self.hub.emit(
                "scene_audio",
                scene_id=scene.id,
                step=i,
                text=speech.text,
                audio=base64.b64encode(speech.wav).decode() if speech.wav else None,
                duration_s=round(speech.duration_s, 2),
            )

    async def _scene_for_job(self, task: str, context: str, agent: AgentProfile) -> dict[str, Any]:
        explain = next(t for t in COMPANION_TOOLS if t.name == "explain_in_3d")
        response = await self.llm.complete(
            system=f"You are {agent.name}. {agent.persona} Build one excellent 3D scene with the explain_in_3d tool.",
            messages=[Message.user(f"Topic: {task}\n\nContext:\n{context}")],
            tools=[explain],
            max_tokens=6000,
        )
        self.turns.add_spend(f"job:{agent.id}", response.usage.cost_usd)
        call = next((c for c in response.tool_calls if c.name == "explain_in_3d"), None)
        if call is None:
            raise RuntimeError("No scene was produced.")
        scene = parse_scene(call.arguments)
        await self.show_scene(scene)
        return scene.model_dump(by_alias=True, mode="json")

    async def time_travel(self, query: str, when: str = "", snapshot_id: int | None = None) -> str:
        """Rebuild a past interface as a 3D hologram, with the surrounding moments on a scrubber."""
        if snapshot_id is None:
            window = parse_when(f"{when} {query}")
            topic = strip_when(query)
            hits = self.spatial.find_snapshots(
                topic, start=window[0] if window else None, end=window[1] if window else None
            )
            if not hits and window:
                hits = self.spatial.find_snapshots(
                    topic
                )  # right thing, wrong day: still better than nothing
            if not hits:
                return f"time_travel: nothing in the Memory Palace matches {query!r}."
            snapshot_id = hits[0]["id"]
        snap = self.spatial.snapshot(snapshot_id)
        if snap is None:
            return "time_travel: that moment has been forgotten."
        scene = snapshot_scene(snap, self.settings.crops_dir, highlight=query)
        timeline = self.spatial.neighbours(snapshot_id)
        await self.hub.emit(
            "palace",
            snapshot_id=snapshot_id,
            timeline=timeline,
            scene=scene.model_dump(by_alias=True, mode="json"),
        )
        self._spawn(self._narrate(scene))
        return f"Time-travelled to {snap['app']} ({snap['title'][:60]}) with {len(scene.nodes)} ghost elements."

    async def emit_pins(self, app: str, ctx: ScreenContext | None = None) -> None:
        """Re-place pinned notes on their elements if they're on screen now."""
        pins = self.activity.pins_for(app)
        if not pins:
            return
        ctx = ctx or await self.screen.capture(pixels=False)
        placed = []
        for pin in pins:
            e = next(
                (
                    x
                    for x in ctx.elements
                    if x.name == pin.element_name and x.role == pin.element_role
                ),
                None,
            )
            if e:
                placed.append({"id": pin.id, "note": pin.note, "color": pin.color, "rect": e.rect})
        await self.hub.emit("pins", app=app, pins=placed)

    async def start_walkthrough(self, job_id: str | None = None) -> None:
        candidates = [j for j in self.jobs.recent(20) if j.role == "browser" and j.run_id]
        job = self.jobs.jobs.get(job_id) if job_id else (candidates[-1] if candidates else None)
        if job is None or not job.run_id:
            await self.say_line("I don't have a finished browser job to walk you through yet.")
            return
        walk = build_walkthrough(self.otto_store, job.run_id)
        await self.hub.emit("walkthrough", walkthrough=walk.model_dump(mode="json"))
        for step in walk.steps:
            speech = await self.tts.synthesize(step.narration)
            await self.hub.emit(
                "walkthrough_audio",
                walkthrough_id=walk.id,
                step=step.index - 1,
                text=speech.text,
                audio=base64.b64encode(speech.wav).decode() if speech.wav else None,
            )

    async def _job_done(self, job: Job) -> None:
        self.turns.add_spend(f"job:{job.agent_id}", job.cost_usd)
        self.activity.log(
            "job", app=job.agent_name, detail=f"{job.agent_name} {job.status}: {job.task[:60]}"
        )
        if job.then and job.status == "done":
            return  # the next teammate takes it from here
        agent = self.registry.get(job.agent_id)
        first = " ".join(split_sentences(job.summary)[:2]) if job.summary else ""
        if job.status == "done":
            line = (
                f"{job.agent_name} here. {first}"
                if job.role != "writer"
                else f"{job.agent_name} here. I wrote a draft for you; it's in the panel."
            )
            if job.role == "browser":
                line += " Want me to show you how I did it?"
        else:
            line = f"{job.agent_name} here. I couldn't finish that: {first or job.status}."
        self.history = [*self.history[-11:], (f"(job {job.id} finished)", line)]
        self._speak(line, agent=job.agent_name, voice=agent.voice if agent else None)

    # ================================================================== misc
    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._bg.add(task)

        def done(t: asyncio.Task[Any]) -> None:
            self._bg.discard(t)
            if not t.cancelled() and (exc := t.exception()):
                log.error("background task failed", exc_info=exc)

        task.add_done_callback(done)

    def _reply_language(self) -> str:
        studio = self.studio
        if not studio:
            return ""
        code = (
            studio.prefs.language if studio.prefs.language != "auto" else studio.detected_language
        )
        return "" if code == "en" else LANGUAGES.get(code, code)

    @property
    def studio(self) -> VoiceStudio | None:
        primary = getattr(self.tts, "primary", self.tts)
        return primary if isinstance(primary, VoiceStudio) else None

    def public_state(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "agents": [a.model_dump() for a in self.registry.all()],
            "jobs": [j.model_dump(mode="json") for j in self.jobs.recent(20)],
            "spent_today": round(self.turns.spent_today(), 4),
            "daily_budget": self.settings.budget.daily_cost_usd,
            "model": self.settings.brain_model,
            "local_mode": self.settings.local_mode,
            "tts": self.studio.engine_name if self.studio else self.tts.name,
            "voice": self.studio.public() if self.studio else None,
            "privacy_paused": self.screen.paused,
            "ambient_memory": self.settings.screen.ambient_memory,
            "keep_screenshots": self.settings.screen.keep_screenshots,
        }


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
