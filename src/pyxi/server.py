"""Pyxi's backend: a FastAPI app the desktop shell talks to over one WebSocket."""

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

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from pyxi import __version__, booking, cloud
from pyxi.companion.brain import Brain
from pyxi.config import PyxiSettings
from pyxi.hub import Hub
from pyxi.palace.tracker import PalaceTracker
from pyxi.scenes import TITLES, get_scene
from pyxi.scenes.object_gallery import OBJECT_GALLERY, OBJECT_TITLES

log = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"^[a-f0-9]{12}\.png$")

# Who may talk to the brain. It sees the screen, drives the mouse and answers its own "Allow"
# cards, so a web page in the user's browser must never reach it: browsers let any site open a
# WebSocket to 127.0.0.1. Pyxi's own windows (the built app is file://, the dev servers are on
# localhost) and local tools that send no Origin at all (scripts, curl) are let in.
APP_ORIGINS = frozenset(
    {
        "file://",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5199",
        "http://127.0.0.1:5199",
    }
)


RESTART = 75  # exit code that asks the desktop app to start the brain again


def origin_allowed(origin: str | None) -> bool:
    extra = {o.strip() for o in os.environ.get("PYXI_ALLOWED_ORIGINS", "").split(",") if o.strip()}
    return origin is None or origin in APP_ORIGINS or origin in extra


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


def has_key(settings: PyxiSettings) -> bool:
    if settings.local_mode:
        return True
    return bool(os.getenv(key_env(settings.model)))


def create_app(
    settings: PyxiSettings | None = None, *, demo: bool | None = None, brain: Brain | None = None
) -> FastAPI:
    settings = settings or PyxiSettings()
    hub = brain.hub if brain is not None else Hub()
    demo = (not has_key(settings)) if demo is None else demo
    demo_site = None
    if brain is None:
        llm = otto_llm = None
        if demo:
            from otto.demo_site import DemoSite
            from otto.demo_site.scripted_demo import demo_provider

            from pyxi.demo import DemoLLM

            demo_site = DemoSite().start()
            llm, otto_llm = DemoLLM(demo_site.url + "/apply"), demo_provider()
        brain = Brain(settings, hub, llm=llm, otto_llm=otto_llm)
    ambient: asyncio.Task[None] | None = None

    async def _routine_clock() -> None:
        while True:
            await asyncio.sleep(30)
            try:
                await brain.run_due_routines()
            except Exception:
                log.exception("routine failed")

    async def _warm_ears() -> None:
        try:
            studio = brain.studio
            await brain.stt.self_test(studio.prefs.language if studio else "en")
        except Exception as exc:  # never block startup on the microphone model
            log.warning("speech model warm-up failed: %s", exc)

    async def _beta_join(code: str) -> None:
        """An invite code from Settings: get a token, then restart the brain so every model call
        goes through the beta server (the app restarts it when it exits with RESTART)."""
        result = await cloud.join(settings.data_dir, code)
        if result != "ok":
            await hub.emit("beta", ok=False, message=result)
            return
        await hub.emit("beta", ok=True, message="You're in. Pyxi is restarting with the beta…")
        asyncio.get_running_loop().call_later(0.8, os._exit, RESTART)

    async def _beta_leave() -> None:
        """Back to the key on this PC: forget the beta token and restart the brain."""
        cloud.leave(settings.data_dir)
        await hub.emit("beta", ok=True, message="Switching to your own key. Pyxi is restarting…")
        asyncio.get_running_loop().call_later(0.8, os._exit, RESTART)

    async def _fetch_voice() -> None:
        """A fresh install has no voice model yet (about 350 MB): fetch it in the background on
        first run, so nobody has to run `pyxi setup-voice`. Until it's there, the OS voice talks."""
        from pyxi.voice.tts import download_kokoro

        models = settings.models_dir
        if (models / "kokoro-v1.0.onnx").exists() and (models / "voices-v1.0.bin").exists():
            return
        if os.environ.get("PYXI_AUTO_VOICE", "1") == "0":
            return
        try:
            log.info("downloading the voice model to %s", models)
            await download_kokoro(models)
            log.info("voice model ready")
            await hub.emit("notice", message="Pyxi's voice is ready.")
        except Exception as exc:  # offline, or the download host is down: try again next start
            log.warning("voice model download failed: %s", exc)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal ambient
        brain.spatial.prune()
        brain.turns.prune(settings.screen.turn_retention_days)
        tracker = PalaceTracker(
            settings.screen,
            brain.screen,
            brain.spatial,
            brain.activity,
            is_busy=lambda: brain.state != "idle",
            on_window=brain.emit_pins,
        )
        ambient = asyncio.create_task(tracker.run())
        routines = asyncio.create_task(_routine_clock())
        # Load the speech model now (in the background), so the first push-to-talk is quick.
        # The OS-voice setup used by tests skips it.
        warm = asyncio.create_task(_warm_ears()) if settings.voice.tts != "client" else None
        voice = asyncio.create_task(_fetch_voice()) if settings.voice.tts == "kokoro" else None
        yield
        if warm:
            warm.cancel()
        if voice:
            voice.cancel()
        ambient.cancel()
        routines.cancel()
        hub.cancel_all()
        if demo_site:
            demo_site.stop()

    app = FastAPI(title="Pyxi", version=__version__, lifespan=lifespan)
    app.state.brain, app.state.hub = brain, hub

    @app.middleware("http")
    async def own_windows_only(request: Request, call_next: Any) -> Any:
        origin = request.headers.get("origin")
        if not origin_allowed(origin):
            return JSONResponse({"detail": "not one of Pyxi's windows"}, status_code=403)
        if request.method == "OPTIONS" and origin:
            response: Any = JSONResponse({})
        else:
            response = await call_next(request)
        if origin:  # Pyxi's own pages read these replies (the Memory tab's timeline)
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response

    def hello() -> dict[str, Any]:
        return {
            **brain.public_state(),
            # The beta: joined (models go through the beta server), or could join. Anyone can join,
            # including people with a key of their own (own_key), whose key then sits unused.
            "beta": {"joined": cloud.in_beta(), "can_join": not cloud.in_beta(), "own_key": cloud.had_own_key()},
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

    @app.get("/api/beta")
    async def beta() -> dict[str, Any]:
        return {"joined": cloud.in_beta(), "allowance": await cloud.allowance(settings.data_dir)}

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
        if not origin_allowed(socket.headers.get("origin")):
            log.warning("refused a connection from %s", socket.headers.get("origin"))
            await socket.close(code=1008)
            return
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
        elif kind == "look_ahead":
            brain.look_ahead()
        elif kind == "profile_get":
            await hub.emit("profile", profile=booking.load(settings.data_dir))
        elif kind == "profile_save" and isinstance(msg.get("profile"), dict):
            saved = booking.save(settings.data_dir, msg["profile"])
            await hub.emit("profile", profile=saved, saved=True)
        elif kind == "beta_join" and str(msg.get("code") or "").strip():
            brain._spawn(_beta_join(str(msg["code"])))
        elif kind == "beta_leave" and cloud.in_beta():
            brain._spawn(_beta_leave())
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
            name = msg.get("name", "pyxi")
            if name in OBJECT_GALLERY:
                await brain.show_object(OBJECT_GALLERY[name](), restart=True)
            else:
                await brain.show_scene(get_scene(name))
        elif kind == "routines":
            await hub.emit("routines", routines=brain.routines.public())
        elif kind == "routine_delete" and msg.get("id"):
            brain.routines.remove(str(msg["id"]))
            await hub.emit("routines", routines=brain.routines.public())
        elif kind == "connectors":
            await hub.emit("connectors", connectors=brain.connectors.statuses())
        elif kind == "connect" and msg.get("id") in ("google", "linkedin"):
            brain._spawn(_connect(str(msg["id"])))
        elif kind == "disconnect" and msg.get("id") in ("google", "linkedin"):
            getattr(brain.connectors, str(msg["id"])).disconnect()
            await hub.emit("connectors", connectors=brain.connectors.statuses())
        elif kind == "guide_clicked":
            how = str(msg.get("how") or "clicked")
            brain._spawn(
                brain.guide_clicked(how if how in ("clicked", "done", "elsewhere") else "clicked")
            )
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
            elif key == "overlay_in_capture":
                brain.overlay_in_capture = bool(value)
            elif key in VOICE_KEYS and brain.studio:
                brain.studio.update(**{VOICE_KEYS[key]: value})
            elif key == "forget_screen":
                brain.spatial.forget_all()
                brain.activity.forget_all()
            await hub.emit("settings", **brain.public_state())

    return app
