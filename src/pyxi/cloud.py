"""The Pyxi beta: no API key on the user's PC.

A beta user types an invite code once (Settings). Pyxi swaps it for a token at the beta server,
saves it in the data folder (cloud.json), and from then on every model call goes to the beta
server, which adds the founder's OpenRouter key and keeps each user inside a daily allowance.
Someone with their own key in .env never touches the beta server.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Where the beta server lives (cloud/ in this repo, deployed on Vercel). Overridable for testing.
DEFAULT_URL = "https://pyxi-beta.vercel.app"
BETA_MODEL = "anthropic/claude-haiku-5.5"
FILE = "cloud.json"


def server_url() -> str:
    return os.environ.get("PYXI_CLOUD_URL", DEFAULT_URL).rstrip("/")


def saved(data_dir: Path) -> dict[str, Any] | None:
    try:
        d = json.loads((data_dir / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and str(d.get("token", "")).startswith("pyxi_") else None


def has_own_key() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def apply(data_dir: Path) -> bool:
    """Before settings load: if there's no key of their own but a beta token, route the models
    through the beta server. Returns whether the beta is in use."""
    if has_own_key():
        return False
    beta = saved(data_dir)
    if beta is None:
        return False
    url = str(beta.get("url") or server_url()).rstrip("/")
    os.environ["OPENROUTER_API_KEY"] = beta["token"]
    os.environ["PYXI_OPENROUTER_URL"] = url + "/api/v1"
    os.environ.setdefault("PYXI_MODEL", BETA_MODEL)
    os.environ["PYXI_BETA"] = "1"
    return True


def in_beta() -> bool:
    return os.environ.get("PYXI_BETA") == "1"


async def join(data_dir: Path, code: str) -> str:
    """Swap an invite code for a token and save it. Returns a line for the user."""
    url = server_url()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url + "/api/join", json={"code": code.strip()})
    except httpx.HTTPError as exc:
        return f"Couldn't reach the Pyxi beta server ({exc.__class__.__name__}). Check your internet and try again."
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "token" not in body:
        return str(body.get("error") or f"The beta server said no ({r.status_code}).")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / FILE).write_text(json.dumps({"url": url, "token": body["token"]}), encoding="utf-8")
    return "ok"


async def allowance(data_dir: Path) -> dict[str, Any] | None:
    """Today's allowance from the beta server, for the panel (None if not in the beta)."""
    beta = saved(data_dir)
    if beta is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(
                str(beta.get("url") or server_url()) + "/api/me",
                headers={"Authorization": f"Bearer {beta['token']}"},
            )
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def leave(data_dir: Path) -> None:
    (data_dir / FILE).unlink(missing_ok=True)
