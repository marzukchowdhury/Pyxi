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
from datetime import datetime
from pathlib import Path
from typing import Any, get_args

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from pyxi import __version__, booking, brainkey, cloud, files_access
from pyxi.companion import pricecheck
from pyxi.companion.brain import Brain
from pyxi.config import PyxiSettings
from pyxi.hub import Hub
from pyxi.palace.tracker import PalaceTracker
from pyxi.routines import Days
from pyxi.scenes import TITLES, get_scene
from pyxi.scenes import library as model_library
from pyxi.scenes.object_gallery import OBJECT_GALLERY, OBJECT_TITLES

log = logging.getLogger(__name__)
ROUTINE_DAYS = set(get_args(Days))
_SAFE_NAME = re.compile(r"^[a-f0-9]{12}\.png$")
_LIBRARY_NAME = re.compile(r"^[a-z0-9_]{1,60}\.glb$")

# Who may talk to the brain. It sees the screen, drives the mouse and answers its own "Allow"
# cards, so two checks guard every request:
# - Origin: a web page in the user's browser must never reach it (browsers let any site open a
#   WebSocket to 127.0.0.1). Pyxi's own windows (the built app is file://, the dev servers are on
#   localhost) and local tools that send no Origin at all are let through this one.
# - The launch key (pyxi.brainkey): any program on the PC can connect to 127.0.0.1 and send no
#   Origin, so every request must also carry the key the desktop app made at launch.
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
    settings: PyxiSettings | None = None,
    *,
    demo: bool | None = None,
    brain: Brain | None = None,
    key: str | None = None,
) -> FastAPI:
    settings = settings or PyxiSettings()
    key = key or brainkey.resolve(settings.data_dir)
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

    async def _prepare_ears() -> None:
        """Have the speech model (and the libraries it needs) on the PC, so the first push-to-talk
        doesn't wait for a download. It isn't loaded until the talk key goes down."""
        try:
            studio = brain.studio
            if studio and studio.wants_bengali():
                await studio.bengali.fetch()  # the Bengali voice, downloaded once (108 MB)
            await asyncio.to_thread(brain.stt.prepare, studio.prefs.language if studio else "en")
        except Exception as exc:  # never block startup on the microphone model
            log.warning("speech model check failed: %s", exc)

    async def _let_go_when_idle() -> None:
        """Speech, voice and OCR models leave memory after a few idle minutes."""
        while True:
            await asyncio.sleep(30)
            try:
                brain.release_idle(settings.voice.unload_after_s)
            except Exception:
                log.exception("releasing idle models failed")

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

    async def _on_window(app: str) -> None:
        await brain.emit_pins(app)
        await pricecheck.on_window(brain, app)  # tags go with their page; auto price checks

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
            on_window=_on_window,
        )
        ambient = asyncio.create_task(tracker.run())
        routines = asyncio.create_task(_routine_clock())
        # Fetch the speech model now if it's missing, so the first push-to-talk is quick.
        # The OS-voice setup used by tests skips it.
        warm = asyncio.create_task(_prepare_ears()) if settings.voice.tts != "client" else None
        idle = asyncio.create_task(_let_go_when_idle())
        voice = asyncio.create_task(_fetch_voice()) if settings.voice.tts == "kokoro" else None
        yield
        if warm:
            warm.cancel()
        if voice:
            voice.cancel()
        ambient.cancel()
        routines.cancel()
        idle.cancel()
        hub.cancel_all()
        if demo_site:
            demo_site.stop()

    app = FastAPI(title="Pyxi", version=__version__, lifespan=lifespan)
    app.state.brain, app.state.hub = brain, hub

    def keyed(headers: Any, query: Any) -> bool:
        return brainkey.matches(key, headers.get(brainkey.HEADER) or query.get(brainkey.QUERY))

    @app.middleware("http")
    async def own_windows_only(request: Request, call_next: Any) -> Any:
        origin = request.headers.get("origin")
        if not origin_allowed(origin):
            return JSONResponse({"detail": "not one of Pyxi's windows"}, status_code=403)
        authorized = keyed(request.headers, request.query_params)
        request.state.authorized = authorized
        if request.method == "OPTIONS" and origin:
            response: Any = JSONResponse({})
            response.headers["Access-Control-Allow-Headers"] = brainkey.HEADER
        elif not authorized and request.url.path != "/api/health":
            return JSONResponse({"detail": "missing or wrong launch key"}, status_code=401)
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
            "beta": {
                "joined": cloud.in_beta(),
                "can_join": not cloud.in_beta(),
                "own_key": cloud.had_own_key(),
            },
            "version": __version__,
            "demo": demo,
            "gallery": {**TITLES, **OBJECT_TITLES},
        }

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        # Open to any local caller, so a starting app can tell "a brain is on this port" from
        # "it's my brain" (authorized: it sent this brain's key).
        return {"ok": True, "version": __version__, "authorized": request.state.authorized}

    @app.post("/api/quit")
    async def quit_brain() -> dict[str, Any]:
        """A newer app asks an older brain to make way (see ensureBrain in the desktop app)."""
        asyncio.get_running_loop().call_later(0.2, os._exit, 0)
        return {"ok": True}

    @app.post("/api/stop")
    async def stop_everything() -> dict[str, Any]:
        """The desktop app's "Stop everything" hotkey and tray item (needs the launch key)."""
        return {"ok": True, "message": await brain.stop_everything()}

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

    @app.get("/library/{name}")
    async def library_model(name: str) -> FileResponse:
        """A real 3D model from the library (downloaded into the data folder when first used)."""
        # Asked for by model id ("concept_car.glb"); the file on disk may carry a version.
        model = model_library.get(name[:-4]) if _LIBRARY_NAME.match(name) else None
        path = settings.library_dir / (model.file if model else name)
        if not _LIBRARY_NAME.match(name) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="model/gltf-binary")

    @app.get("/crops/{name}")
    async def crop(name: str) -> FileResponse:
        path = settings.crops_dir / name
        if not _SAFE_NAME.match(name) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    @app.get("/file")
    async def file(path: str) -> FileResponse:
        target = Path(path).resolve()
        # Pictures Pyxi saved (walkthrough steps) only: never its tokens, keys or databases.
        if (
            not target.is_relative_to(settings.data_dir.resolve())
            or target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp")
            or not target.is_file()
        ):
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
        if not keyed(socket.headers, socket.query_params):
            log.warning("refused a connection without the launch key")
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

    async def _e2e_card() -> None:
        """End-to-end tests only (demo mode with PYXI_E2E=1): a harmless Allow card, so a test can
        answer it in the real overlay. The answer is broadcast back."""
        value = await hub.request(
            "confirm", timeout=120, job_id="", agent="Pyxi", action="E2E check", reason="Test card."
        )
        await hub.emit("e2e_answer", value=value)

    async def handle(msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "e2e_card" and demo and os.environ.get("PYXI_E2E") == "1":
            brain._spawn(_e2e_card())
        elif kind == "text" and msg.get("text", "").strip():
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
        elif kind == "shopping_get":
            await hub.emit("shopping", **pricecheck.load(settings.data_dir))
        elif kind == "shopping_save":
            await hub.emit("shopping", **pricecheck.save(settings.data_dir, msg))
        elif kind in ("memories_get", "memory_forget", "memories_wipe"):
            if kind == "memory_forget":
                brain.forget_memory(
                    str(msg.get("store") or "pyxi"),
                    str(msg.get("scope") or ""),
                    str(msg.get("key") or ""),
                )
            elif kind == "memories_wipe":

                async def _wipe() -> None:
                    await hub.emit("notice", message=await brain.wipe_everything())
                    await hub.emit("memories", memories=brain.memories_public())

                brain._spawn(_wipe())
            await hub.emit("memories", memories=brain.memories_public())
        elif kind in ("audit_get", "audit_undo"):
            if kind == "audit_undo" and msg.get("id"):
                done = await asyncio.to_thread(brain.audit.undo, str(msg["id"]))
                await hub.emit("notice", message=done)
            await hub.emit("audit", entries=brain.audit.recent(200))
        elif kind in ("grants_get", "grant_revoke"):
            from pyxi import grants

            if kind == "grant_revoke":
                grants.revoke(settings.data_dir, str(msg.get("kind") or ""))
            await hub.emit("grants", grants=grants.public(settings.data_dir))
        elif kind in ("private_apps_get", "private_apps_save"):
            from pyxi.screen import private_apps

            apps = (
                private_apps.save(settings.data_dir, msg.get("apps"))
                if kind == "private_apps_save"
                else private_apps.load(settings.data_dir)
            )
            if hasattr(brain.screen, "set_private_apps"):
                brain.screen.set_private_apps(apps)
            await hub.emit("private_apps", apps=apps, built_in=private_apps.BUILT_IN)
        elif kind == "file_access_get":
            await hub.emit("file_access", **files_access.load(settings.data_dir).public())
        elif kind == "file_access_save":
            access = files_access.load(settings.data_dir)
            access.enabled = bool(msg.get("enabled"))
            access.asked = True  # they've decided in Settings: no consent card needed
            if isinstance(msg.get("folders"), list):
                access.folders = [str(f) for f in msg["folders"]]
            saved = files_access.save(settings.data_dir, access)
            await hub.emit("file_access", **saved.public())
        elif kind in ("libreoffice_get", "libreoffice_set"):
            from pyxi.agents import libreoffice

            converter = brain.jobs.converter()
            if kind == "libreoffice_set":
                allowed = bool(msg.get("allowed"))
                libreoffice.save_choice(settings.data_dir, libreoffice.Choice(True, allowed))
                if allowed:
                    converter.start()  # downloads in the background; progress comes as events
            choice = libreoffice.load_choice(settings.data_dir)
            await hub.emit("libreoffice", **converter.status.public(), **vars(choice))
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
        elif kind == "stop_everything":
            await brain.stop_everything()
        elif kind == "answer":
            from pyxi import hands

            if hands.recently_acted():
                # Pyxi's own hands were moving: this "click" may be its own. Pyxi never
                # approves its own cards, so the card stays until the user answers it.
                log.warning("ignored a card answer during Pyxi's own mouse or keyboard input")
                await hub.emit(
                    "error",
                    message="Pyxi was using the mouse just then, so that didn't count. "
                    "Answer the card yourself.",
                )
                return
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
        elif kind == "routine_add" and str(msg.get("task") or "").strip():
            # The Routines tab's own form: what, which days, what time (or every N minutes).
            days = [d for d in msg.get("days") or [] if d in ROUTINE_DAYS] or ["every day"]
            every = msg.get("every_minutes")
            brain.routines.add(
                str(msg["task"]),
                days=days,
                time=None if every else str(msg.get("time") or "09:00"),
                every_minutes=int(every) if every else None,
            )
            await hub.emit("routines", routines=brain.routines.public())
        elif kind == "routine_toggle" and msg.get("id"):
            for r in brain.routines.items:
                if r.id == msg["id"]:
                    r.enabled = bool(msg.get("enabled"))
                    if r.enabled:
                        r.next_run = r.compute_next(datetime.now())
                    brain.routines.save()
            await hub.emit("routines", routines=brain.routines.public())
        elif kind == "routine_run" and msg.get("id"):
            r = next((r for r in brain.routines.items if r.id == msg["id"]), None)
            if r is not None and r.kind == "turn":
                brain._spawn(
                    brain.turn(
                        f"(Routine: {r.task})", shown=f"Routine: {r.task[:80]}", synthetic=True
                    )
                )
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
