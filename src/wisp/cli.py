"""Wisp CLI: run the backend, set up voice, and poke at each layer from the terminal."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from wisp import __version__
from wisp.config import WispSettings

app = typer.Typer(help="Wisp: your playful desktop AI buddy.", no_args_is_help=True)
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

    from wisp.server import create_app

    _load_dotenv()
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    s = WispSettings()
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
        f"Wisp brain on [bold]ws://{s.host}:{s.port}/ws[/] · {mode} · voice {s.voice.tts}"
    )
    uvicorn.run(app_, host=s.host, port=s.port, log_level="warning")


@app.command("setup-voice")
def setup_voice() -> None:
    """Download Kokoro-82M (about 350 MB) for local text-to-speech."""
    from wisp.voice.tts import download_kokoro

    s = WispSettings()
    console.print("Downloading Kokoro-82M voice model…")
    for path in asyncio.run(download_kokoro(s.models_dir)):
        console.print(f"  [green]✓[/] {path} ({path.stat().st_size / 1e6:.0f} MB)")


@app.command()
def say(
    text: str, voice: str = typer.Option(None), out: str = typer.Option("wisp-say.wav")
) -> None:
    """Synthesize a line with the configured TTS and save it as a WAV."""
    from wisp.voice import make_tts

    s = WispSettings()
    speech = asyncio.run(make_tts(s.voice, s.models_dir).synthesize(text, voice))
    if speech.wav:
        Path(out).write_bytes(speech.wav)
        console.print(f"Saved {out} ({speech.duration_s:.1f}s)")
    else:
        console.print("This TTS provider speaks on the client; nothing to save.")


@app.command()
def look() -> None:
    """Print what Wisp sees right now (foreground window + accessibility tree)."""
    from wisp.screen import ScreenReader

    ctx = asyncio.run(ScreenReader(WispSettings().screen).capture(pixels=False))
    console.print(ctx.render(80), highlight=False)


@app.command()
def scenes() -> None:
    """List the built-in 3D gallery scenes."""
    from wisp.scenes import TITLES

    for name, title in TITLES.items():
        console.print(f"[bold]{name}[/]  {title}")


@app.command()
def doctor() -> None:
    """Check keys, voice models, GPU and Ollama."""
    _load_dotenv()
    s = WispSettings()
    rows = [
        ("Wisp", __version__),
        ("Brain", s.brain_model + (" (local)" if s.local_mode else "")),
        (
            "ANTHROPIC_API_KEY",
            "set" if os.getenv("ANTHROPIC_API_KEY") else "missing (demo mode will be used)",
        ),
    ]
    kokoro = (s.models_dir / "kokoro-v1.0.onnx").exists()
    rows.append(("Kokoro voice", "installed" if kokoro else "missing: run `wisp setup-voice`"))
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
