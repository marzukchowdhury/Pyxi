"""How much memory and CPU does Pyxi take? A repeatable check, before and after a change.

Two ways to run it:

    python scripts/perf_check.py              # the Pyxi that is running now (installed or dev)
    python scripts/perf_check.py --launch     # start a brain from this repo, in demo mode
    python scripts/perf_check.py --desktop    # start the dev desktop app (desktop/out) + brain

For each Pyxi process it prints:
- memory: the private working set, the number Task Manager shows in its Memory column;
- commit: private bytes, what the process has asked Windows for (RAM plus page file);
- cpu: percent of one core, averaged over the sample window.

With --launch it starts `pyxi serve --demo` on a spare port with a throwaway data folder (the
voice models are borrowed from .pyxi/models), so no model is called and nothing costs credits.
It measures the brain idle after start-up, then sends one spoken question over the WebSocket
(the same message the overlay sends), times the first transcript and the first voice line,
and measures again right after the turn and, with --after N, N seconds later (to see idle
unloading). It stops the brain when done.

usage: python scripts/perf_check.py [--launch] [--window S] [--settle S] [--after S] [--json]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parents[1]
QUESTION = "Hey Pyxi, what time is it where I am?"
MB = 1024 * 1024


def kind_of(p: psutil.Process) -> str | None:
    """'brain', 'electron:<type>' or None when the process isn't Pyxi's."""
    try:
        name = (p.name() or "").lower()
        cmd = p.cmdline()
    except (psutil.Error, OSError):
        return None
    line = " ".join(cmd).lower()
    if name.startswith("python") and "pyxi" in line and "serve" in line:
        return "brain"
    if name in ("pyxi.exe", "electron.exe") and ("pyxi" in line or "wisp" in line):
        for part in cmd:
            if part.startswith("--type="):
                sub = next((c for c in cmd if c.startswith("--utility-sub-type=")), "")
                t = part[7:]
                if sub:
                    t += ":" + sub.split("=", 1)[1].split(".")[0]
                return "electron:" + t
        return "electron:main"
    return None


def pyxi_processes(only_pid: int | None = None) -> list[tuple[psutil.Process, str]]:
    found = []
    for p in psutil.process_iter():
        if only_pid is not None and p.pid != only_pid:
            continue
        k = kind_of(p)
        if k:
            found.append((p, k))
    return found


def sample(procs: list[tuple[psutil.Process, str]], window: float) -> list[dict]:
    """Memory now, CPU averaged over ``window`` seconds."""
    start = {}
    for p, _ in procs:
        try:
            start[p.pid] = p.cpu_times()
        except psutil.Error:
            pass
    t0 = time.monotonic()
    time.sleep(window)
    span = time.monotonic() - t0
    rows = []
    for p, k in procs:
        try:
            full = p.memory_full_info()
            ct = p.cpu_times()
        except psutil.Error:
            continue
        before = start.get(p.pid)
        busy = (ct.user + ct.system - before.user - before.system) if before else 0.0
        rows.append(
            {
                "pid": p.pid,
                "kind": k,
                "memory_mb": round(full.uss / MB),
                "commit_mb": round(getattr(full, "private", full.uss) / MB),
                "cpu_pct": round(100 * busy / span, 1),
                "threads": p.num_threads(),
            }
        )
    rows.sort(key=lambda r: (r["kind"] != "brain", r["kind"], -r["memory_mb"]))
    return rows


def report(label: str, rows: list[dict]) -> None:
    print(f"\n== {label}")
    print(f"{'pid':>7}  {'process':34s} {'memory':>8} {'commit':>8} {'cpu %':>7}")
    for r in rows:
        print(
            f"{r['pid']:>7}  {r['kind']:34s} {r['memory_mb']:>6} MB {r['commit_mb']:>6} MB"
            f" {r['cpu_pct']:>7.1f}"
        )
    mem = sum(r["memory_mb"] for r in rows)
    com = sum(r["commit_mb"] for r in rows)
    cpu = sum(r["cpu_pct"] for r in rows)
    print(f"{'':7}  {'total':34s} {mem:>6} MB {com:>6} MB {cpu:>7.1f}")


def question_wav(cache: Path) -> bytes:
    """A spoken question, made once with Pyxi's own voice (in this process, not the brain)."""
    if cache.exists():
        return cache.read_bytes()
    sys.path.insert(0, str(REPO / "src"))
    from pyxi.config import VoiceConfig
    from pyxi.voice.tts import make_tts

    cfg = VoiceConfig()
    cfg.voice = "am_puck"
    tts = make_tts(cfg, REPO / ".pyxi" / "models", Path(tempfile.mkdtemp()) / "voice.json")
    speech = asyncio.run(tts.synthesize(QUESTION))
    if not speech.wav:
        raise SystemExit("no voice model in .pyxi/models: run `pyxi setup-voice` first")
    cache.write_bytes(speech.wav)
    return speech.wav


def speaking_s(wav: bytes) -> float:
    import io

    import soundfile as sf

    return sf.info(io.BytesIO(wav)).duration


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def voice_turn(port: int, key: str, wav: bytes, timeout: float = 120) -> dict[str, float]:
    import websockets

    marks: dict[str, float] = {}
    url = f"ws://127.0.0.1:{port}/ws?key={key}"
    async with websockets.connect(url, max_size=None) as ws:
        await ws.recv()  # hello
        # As the overlay does: the talk key goes down, they speak, the key comes up, the clip goes.
        await ws.send(json.dumps({"type": "ptt_start"}))
        await asyncio.sleep(speaking_s(wav))
        t0 = time.monotonic()  # counted from the key coming up, like scripts/latency.py
        await ws.send(json.dumps({"type": "audio", "data": base64.b64encode(wav).decode()}))
        thinking = False
        while time.monotonic() - t0 < timeout:
            try:
                e = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            except TimeoutError:
                break
            now = round(time.monotonic() - t0, 2)
            if e.get("type") == "transcript":
                marks.setdefault("heard_s", now)
                marks["text"] = e.get("text", "")  # type: ignore[assignment]
            elif e.get("type") == "say":
                marks.setdefault("first_voice_s", now)
            elif e.get("type") == "state":
                if e.get("state") != "idle":
                    thinking = True
                elif thinking:
                    marks["done_s"] = now
                    break
    return marks


def wait_healthy(port: int, proc: subprocess.Popen, limit: float = 120) -> float:
    import urllib.request

    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if proc.poll() is not None:
            raise SystemExit(f"the brain exited ({proc.returncode})")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
            return time.monotonic() - t0
        except Exception:
            time.sleep(0.3)
    raise SystemExit("the brain didn't come up")


def launch(args: argparse.Namespace) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="pyxi_perf_"))
    data = tmp / ".pyxi"
    data.mkdir()
    models = REPO / ".pyxi" / "models"
    if models.exists():  # borrow the voice models without copying 350 MB
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(data / "models"), str(models)],
            check=True,
            capture_output=True,
        )
    port, key = free_port(), secrets.token_urlsafe(24)
    py = REPO / ".venv" / "Scripts" / "python.exe"
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYXI_DATA_DIR": str(data),
        "PYXI_BRAIN_KEY": key,
        "PYXI_AUTO_VOICE": "0",
        "PYXI_TOUR": "0",  # an empty data folder: no first-run tour over what is measured
        **args.env,
    }
    log = open(tmp / "brain.log", "w", encoding="utf-8")  # noqa: SIM115 (open while the brain runs)
    t_start = time.monotonic()
    proc = subprocess.Popen(
        [str(py), "-m", "pyxi", "serve", "--demo", "--port", str(port), "-v"],
        cwd=tmp,  # so the repo's .env and pyxi.yaml stay out of it
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    out: dict = {}
    try:
        out["ready_s"] = round(wait_healthy(port, proc), 1)
        if args.cores:  # a weaker PC: the brain gets only this many cores
            for c in [psutil.Process(proc.pid), *psutil.Process(proc.pid).children(recursive=True)]:
                c.cpu_affinity(list(range(args.cores)))
        wav = question_wav(Path(tempfile.gettempdir()) / "pyxi_perf_question.wav")
        time.sleep(args.settle)  # start-up work (warm-up, first screen look) settles
        root = psutil.Process(proc.pid)  # a venv's python.exe is a stub that starts the real one
        procs = [
            (c, "brain")
            for c in [root, *root.children(recursive=True)]
            if c.memory_info().rss > 30 * MB
        ]
        out["idle"] = sample(procs, args.window)
        report(f"brain idle, {args.settle:.0f} s after start", out["idle"])
        out["turn"] = asyncio.run(voice_turn(port, key, wav))
        print(f"\nfirst voice turn: {out['turn']}")
        out["after_turn"] = sample(procs, args.window)
        report("brain right after one voice turn", out["after_turn"])
        out["turn2"] = asyncio.run(voice_turn(port, key, wav))
        print(f"\nsecond voice turn: {out['turn2']}")
        if args.after:
            time.sleep(max(0.0, args.after - args.window))
            out["later"] = sample(procs, args.window)
            report(f"brain {args.after:.0f} s after the turn", out["later"])
            out["turn3"] = asyncio.run(voice_turn(port, key, wav))
            print(f"\nvoice turn after that wait: {out['turn3']}")
        out["started_in_s"] = round(time.monotonic() - t_start, 1)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        if args.keep_log:
            print(f"log: {tmp / 'brain.log'}")
        else:
            link = data / "models"
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)
            if not (link.exists() or os.path.isjunction(link)):  # never delete through it
                shutil.rmtree(tmp, ignore_errors=True)
    return out


def tree(pid: int) -> list[tuple[psutil.Process, str]]:
    root = psutil.Process(pid)
    out = []
    for p in [root, *root.children(recursive=True)]:
        try:
            if p.memory_info().rss < 5 * MB:  # launcher stubs, conhost
                continue
            name = p.name().lower()
        except psutil.Error:
            continue
        k = kind_of(p) or ("brain" if name.startswith("python") else name)
        out.append((p, k))
    return out


def launch_desktop(args: argparse.Namespace) -> dict:
    """The dev Electron app (desktop/out, from `npm run build`) with a demo brain of its own."""
    tmp = Path(tempfile.mkdtemp(prefix="pyxi_perf_app_"))
    data = tmp / ".pyxi"
    data.mkdir()
    port = free_port()
    electron = REPO / "desktop" / "node_modules" / "electron" / "dist" / "electron.exe"
    env = {
        **os.environ,
        "PYXI_BACKEND": f"http://127.0.0.1:{port}",
        "PYXI_ARGS": f"--demo --port {port}",
        "PYXI_DATA_DIR": str(data),
        "PYXI_AUTO_VOICE": "0",
        "PYXI_TOUR": "0",  # an empty data folder: no first-run tour over what is measured
        **args.env,
    }
    env.pop("ELECTRON_RUN_AS_NODE", None)
    proc = subprocess.Popen([str(electron), "."], cwd=REPO / "desktop", env=env)
    out: dict = {}
    try:
        time.sleep(args.settle)
        out["idle"] = sample(tree(proc.pid), args.window)
        report(f"desktop app idle, {args.settle:.0f} s after start", out["idle"])
        if args.after:
            time.sleep(args.after)
            out["later"] = sample(tree(proc.pid), args.window)
            report(f"desktop app {args.after + args.settle:.0f} s after start", out["later"])
    finally:
        for p, _ in tree(proc.pid)[::-1]:
            try:
                p.kill()
            except psutil.Error:
                pass
        proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--launch", action="store_true", help="start a demo brain from this repo")
    ap.add_argument("--desktop", action="store_true", help="start the dev desktop app (demo)")
    ap.add_argument("--pid", type=int, help="measure this process and its children")
    ap.add_argument("--window", type=float, default=10.0, help="CPU sample window, seconds")
    ap.add_argument("--settle", type=float, default=20.0, help="wait after start (--launch)")
    ap.add_argument("--after", type=float, default=0.0, help="measure again this long after")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE for the brain")
    ap.add_argument("--cores", type=int, default=0, help="limit the brain to N cores (--launch)")
    ap.add_argument("--keep-log", action="store_true")
    ap.add_argument("--json", action="store_true", help="also print the numbers as JSON")
    args = ap.parse_args()
    args.env = dict(e.split("=", 1) for e in args.env)
    if args.desktop:
        out = launch_desktop(args)
    elif args.launch:
        out = launch(args)
    elif args.pid:
        out = {"now": sample(tree(args.pid), args.window)}
        report(f"process {args.pid} and its children (cpu over {args.window:.0f} s)", out["now"])
    else:
        procs = pyxi_processes()
        if not procs:
            raise SystemExit("no Pyxi processes running")
        out = {"now": sample(procs, args.window)}
        report(f"Pyxi now (cpu over {args.window:.0f} s)", out["now"])
    if args.json:
        print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
