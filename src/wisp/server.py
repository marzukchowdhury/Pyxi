"""Wisp's backend: a FastAPI app the desktop shell talks to over one WebSocket."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from wisp import __version__
from wisp.companion.brain import Brain
from wisp.config import WispSettings
from wisp.hub import Hub
from wisp.palace.tracker import PalaceTracker
from wisp.scenes import TITLES, get_scene
from wisp.scenes.object_gallery import OBJECT_GALLERY, OBJECT_TITLES

log = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"^[a-f0-9]{12}\.png$")


# Panel setting keys -> VoicePrefs fields.
VOICE_KEYS = {
    "voice_engine": "engine",
    "voice": "voice",
    "voice_speed": "speed",
    "voice_language": "language",
    "captions": "captions",
}


def key_env(model: str) -> str:
    """Which environment variable holds the key for ``model``."""
    from otto.config import infer_provider

    return {"anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}.get(
        infer_provider(model), "OPENAI_API_KEY"
    )


def has_key(settings: WispSettings) -> bool:
    if settings.local_mode:
        return True
    return bool(os.getenv(key_env(settings.model)))


def create_app(
    settings: WispSettings | None = None, *, demo: bool | None = None, brain: Brain | None = None
) -> FastAPI:
    settings = settings or WispSettings()
    hub = brain.hub if brain is not None else Hub()
    demo = (not has_key(settings)) if demo is None else demo
    demo_site = None
    if brain is None:
        llm = otto_llm = None
        if demo:
            from otto.demo_site import DemoSite
            from otto.demo_site.scripted_demo import demo_provider

            from wisp.demo import DemoLLM

            demo_site = DemoSite().start()
            llm, otto_llm = DemoLLM(demo_site.url + "/apply"), demo_provider()
        brain = Brain(settings, hub, llm=llm, otto_llm=otto_llm)
    ambient: asyncio.Task[None] | None = None

    async def _warm_ears() -> None:
        try:
            studio = brain.studio
            await brain.stt.self_test(studio.prefs.language if studio else "en")
        except Exception as exc:  # never block startup on the microphone model
            log.warning("speech model warm-up failed: %s", exc)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal ambient
        brain.spatial.prune()
        tracker = PalaceTracker(
            settings.screen,
            brain.screen,
            brain.spatial,
            brain.activity,
            is_busy=lambda: brain.state != "idle",
            on_window=brain.emit_pins,
        )
        ambient = asyncio.create_task(tracker.run())
        # Load the speech model now (in the background), so the first push-to-talk is quick.
        # The OS-voice setup used by tests skips it.
        warm = asyncio.create_task(_warm_ears()) if settings.voice.tts != "client" else None
        yield
        if warm:
            warm.cancel()
        ambient.cancel()
        hub.cancel_all()
        if demo_site:
            demo_site.stop()

    app = FastAPI(title="Wisp", version=__version__, lifespan=lifespan)
    app.state.brain, app.state.hub = brain, hub

    def hello() -> dict[str, Any]:
        return {
            **brain.public_state(),
            "version": __version__,
            "demo": demo,
            "gallery": {**TITLES, **OBJECT_TITLES},
        }

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return hello()

    @app.get("/api/turns")
    async def turns(limit: int = 30) -> list[dict[str, Any]]:
        return brain.turns.recent(limit)

    @app.get("/api/timeline")
    async def timeline(limit: int = 40) -> list[dict[str, Any]]:
        return brain.spatial.timeline(limit)

    @app.get("/api/activity")
    async def activity(minutes: float = 120) -> list[dict[str, Any]]:
        return [
            {**e.__dict__, "headline": e.headline()} for e in brain.activity.recent(minutes, 200)
        ]

    @app.get("/api/scene/{name}")
    async def scene(name: str) -> dict[str, Any]:
        try:
            return get_scene(name).model_dump(by_alias=True, mode="json")
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/crops/{name}")
    async def crop(name: str) -> FileResponse:
        path = settings.crops_dir / name
        if not _SAFE_NAME.match(name) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    @app.get("/file")
    async def file(path: str) -> FileResponse:
        target = Path(path).resolve()
        if not target.is_relative_to(settings.data_dir.resolve()) or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    async def _connect(service: str) -> None:
        """Run the service's browser sign-in (it opens on this PC) and report back."""
        connector = getattr(brain.connectors, service)
        try:
            who = await asyncio.to_thread(connector.sign_in)
            message = f"Connected {connector.name} as {who}."
        except Exception as exc:
            message = f"Couldn't connect {connector.name}: {exc}"
        await hub.emit("connectors", connectors=brain.connectors.statuses(), message=message)

    async def _preview(msg: dict[str, Any]) -> None:
        """Speak a sample line in a voice without changing the setting (the voice picker)."""
        studio = brain.studio
        if studio is None:
            return
        engine, voice = str(msg.get("engine", "kokoro")), str(msg.get("voice", ""))
        speech = await studio.preview(engine, voice, msg.get("speed"))
        await hub.emit(
            "voice_preview",
            engine=engine,
            voice=voice,
            text=speech.text,
            audio=base64.b64encode(speech.wav).decode() if speech.wav else None,
            error=studio.last_error,
        )

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        cid = hub.connect(socket.send_text)
        await socket.send_text(json.dumps({"type": "hello", **hello()}, default=str))
        try:
            while True:
                msg = json.loads(await socket.receive_text())
                await handle(msg)
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(cid)

    async def handle(msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "text" and msg.get("text", "").strip():
            brain._spawn(brain.turn(msg["text"].strip()))
        elif kind == "ptt_start":
            brain.begin_listening()
        elif kind == "audio" and msg.get("data"):
            brain._spawn(brain.hear(base64.b64decode(msg["data"])))
        elif kind == "ptt_cancel":
            brain._prefetch = None
            await brain.set_state("idle")
        elif kind == "interrupt":
            brain.interrupt()
        elif kind == "answer":
            hub.resolve(msg.get("request_id", ""), msg.get("value"))
        elif kind == "job_control":
            brain.jobs.control(msg.get("job_id", ""), msg.get("action", ""), msg.get("text", ""))
        elif kind == "show_scene":
            name = msg.get("name", "wisp")
            if name in OBJECT_GALLERY:
                await brain.show_object(OBJECT_GALLERY[name](), restart=True)
            else:
                await brain.show_scene(get_scene(name))
        elif kind == "connectors":
            await hub.emit("connectors", connectors=brain.connectors.statuses())
        elif kind == "connect" and msg.get("id") in ("google", "linkedin"):
            brain._spawn(_connect(str(msg["id"])))
        elif kind == "disconnect" and msg.get("id") in ("google", "linkedin"):
            getattr(brain.connectors, str(msg["id"])).disconnect()
            await hub.emit("connectors", connectors=brain.connectors.statuses())
        elif kind == "guide_clicked":
            brain._spawn(brain.guide_clicked())
        elif kind == "guide_cancel":
            brain._guide = None
        elif kind == "voice_catalog" and brain.studio:
            await hub.emit(
                "voice_catalog", catalog=await brain.studio.catalog(), voice=brain.studio.public()
            )
        elif kind == "voice_preview" and brain.studio:
            brain._spawn(_preview(msg))
        elif kind == "palace_seek" and msg.get("snapshot_id"):
            brain._spawn(brain.time_travel("", snapshot_id=int(msg["snapshot_id"])))
        elif kind == "time_travel":
            brain._spawn(brain.time_travel(msg.get("query", ""), msg.get("when", "")))
        elif kind == "walkthrough":
            brain._spawn(brain.start_walkthrough(msg.get("job_id")))
        elif kind == "clear":
            await hub.emit("clear")
        elif kind == "notice" and msg.get("message"):
            await hub.emit("error", message=str(msg["message"])[:400])
        elif kind == "agent_create":
            brain.registry.create(
                msg["name"],
                msg.get("role", "browser"),
                msg.get("persona", ""),
                **({"voice": msg["voice"]} if msg.get("voice") else {}),
            )
            await hub.emit("agents", agents=[a.model_dump() for a in brain.registry.all()])
        elif kind == "agent_delete":
            brain.registry.delete(msg.get("id", ""))
            await hub.emit("agents", agents=[a.model_dump() for a in brain.registry.all()])
        elif kind == "set":
            key, value = msg.get("key"), msg.get("value")
            if key == "privacy_paused":
                brain.screen.paused = bool(value)
            elif key == "ambient_memory":
                settings.screen.ambient_memory = bool(value)
            elif key == "jobs_headed":
                brain.jobs.headed = bool(value)
            elif key == "keep_screenshots":
                settings.screen.keep_screenshots = bool(value)
                brain.spatial.thumbs_dir = settings.palace_dir if value else None
            elif key in VOICE_KEYS and brain.studio:
                brain.studio.update(**{VOICE_KEYS[key]: value})
            elif key == "forget_screen":
                brain.spatial.forget_all()
                brain.activity.forget_all()
            await hub.emit("settings", **brain.public_state())

    return app
