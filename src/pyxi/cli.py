"""Pyxi CLI: run the backend, set up voice, and poke at each layer from the terminal."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pyxi import __version__
from pyxi.config import PyxiSettings

app = typer.Typer(help="Pyxi: your playful desktop AI buddy.", no_args_is_help=True)
console = Console()
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _load_dotenv() -> None:
    from otto.config import load_dotenv

    load_dotenv()


@app.command()
def serve(
    port: int = typer.Option(None, help="Port (default 8766)."),
    local: bool = typer.Option(
        False, "--local", help="Local mode: Ollama vision model, nothing leaves the machine."
    ),
    demo: bool = typer.Option(
        False, "--demo", help="Demo mode: no model at all, a rule-based stand-in."
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Start the backend the desktop app connects to."""
    import uvicorn

    from pyxi.server import create_app

    _load_dotenv()
    from pyxi import cloud

    cloud.apply(Path(os.environ.get("PYXI_DATA_DIR", ".pyxi")))  # joined the beta: models go through it
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    s = PyxiSettings()
    if local:
        s = s.model_copy(update={"local_mode": True})
    if port:
        s = s.model_copy(update={"port": port})
    app_ = create_app(s, demo=True if demo else None)
    mode = (
        "demo"
        if app_.state.brain.llm.model == "demo"
        else ("local " + s.local_model if s.local_mode else s.model)
    )
    console.print(
        f"Pyxi brain on [bold]ws://{s.host}:{s.port}/ws[/] · {mode} · voice {s.voice.tts}"
    )
    uvicorn.run(app_, host=s.host, port=s.port, log_level="warning")


@app.command("setup-voice")
def setup_voice() -> None:
    """Download Kokoro-82M (about 350 MB) for local text-to-speech."""
    from pyxi.voice.tts import download_kokoro

    s = PyxiSettings()
    console.print("Downloading Kokoro-82M voice model…")
    for path in asyncio.run(download_kokoro(s.models_dir)):
        console.print(f"  [green]✓[/] {path} ({path.stat().st_size / 1e6:.0f} MB)")


@app.command()
def say(
    text: str, voice: str = typer.Option(None), out: str = typer.Option("pyxi-say.wav")
) -> None:
    """Synthesize a line with the configured TTS and save it as a WAV."""
    from pyxi.voice import make_tts

    s = PyxiSettings()
    speech = asyncio.run(make_tts(s.voice, s.models_dir).synthesize(text, voice))
    if speech.wav:
        Path(out).write_bytes(speech.wav)
        console.print(f"Saved {out} ({speech.duration_s:.1f}s)")
    else:
        console.print("This TTS provider speaks on the client; nothing to save.")


@app.command()
def connect(service: str = typer.Argument(..., help="google, linkedin, or status")) -> None:
    """Connect an account (opens the service's own sign-in in your browser)."""
    _load_dotenv()
    from pyxi.connectors import Connectors

    s = PyxiSettings()
    hub = Connectors(s.data_dir, Path.home() / "Documents" / "Pyxi")
    if service == "status":
        for st in hub.statuses():
            mark = "[green]connected[/]" if st["connected"] else "[yellow]not connected[/]"
            console.print(f"{st['name']}: {mark}  {st['detail']}")
            if not st["connected"]:
                for i, step in enumerate(st["setup"], 1):
                    console.print(f"   {i}. {step}")
        return
    connector = {"google": hub.google, "linkedin": hub.linkedin}.get(service)
    if connector is None:
        console.print(
            "Use: pyxi connect google | linkedin | status (Discord uses DISCORD_BOT_TOKEN in .env)."
        )
        raise typer.Exit(1)
    try:
        who = connector.sign_in()
    except Exception as exc:
        console.print(f"[red]Couldn't connect:[/] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Connected {connector.name} as {who}.[/]")


@app.command()
def look() -> None:
    """Print what Pyxi sees right now (foreground window + accessibility tree)."""
    from pyxi.screen import ScreenReader

    ctx = asyncio.run(ScreenReader(PyxiSettings().screen).capture(pixels=False))
    console.print(ctx.render(80), highlight=False)


@app.command()
def scenes() -> None:
    """List the built-in 3D gallery scenes."""
    from pyxi.scenes import TITLES

    for name, title in TITLES.items():
        console.print(f"[bold]{name}[/]  {title}")


@app.command()
def doctor() -> None:
    """Check keys, voice models, GPU and Ollama."""
    _load_dotenv()
    s = PyxiSettings()
    rows = [
        ("Pyxi", __version__),
        ("Brain", s.brain_model + (" (local)" if s.local_mode else "")),
    ]
    from pyxi.server import key_env

    needed = key_env(s.model)
    for env in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        state = "set" if os.getenv(env) else "missing"
        if env == needed and not os.getenv(env) and not s.local_mode:
            state += " (needed for this model; demo mode will be used)"
        rows.append((env, state))
    kokoro = (s.models_dir / "kokoro-v1.0.onnx").exists()
    rows.append(("Kokoro voice", "installed" if kokoro else "missing: run `pyxi setup-voice`"))
    try:
        import ctranslate2

        rows.append(("CUDA for Whisper", f"{ctranslate2.get_cuda_device_count()} device(s)"))
    except Exception:
        rows.append(("CUDA for Whisper", "unavailable (CPU is fine)"))
    try:
        import httpx

        tags = httpx.get(s.local_base_url.rsplit("/v1", 1)[0] + "/api/tags", timeout=2).json()
        names = [m["name"] for m in tags.get("models", [])]
        vision = [n for n in names if any(k in n for k in ("vl", "vision", "llava", "gemma3"))]
        rows.append(
            (
                "Ollama",
                f"running; vision models: {', '.join(vision) or 'none (ollama pull qwen2.5vl:7b)'}",
            )
        )
    except Exception:
        rows.append(("Ollama", "not running (only needed for --local)"))
    table = Table(show_header=False)
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)


if __name__ == "__main__":  # pragma: no cover
    app()
