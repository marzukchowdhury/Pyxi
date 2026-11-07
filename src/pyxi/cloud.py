"""The Pyxi beta: no API key on the user's PC.

A beta user types an invite code once (Settings). Pyxi swaps it for a token at the beta server,
saves it in the data folder (cloud.json, encrypted with Windows DPAPI), and from then on every model call goes to the beta
server, which adds the founder's OpenRouter key and keeps each user inside a daily allowance.
Joining is a deliberate choice, so once someone has joined, the beta wins even if a key of their
own is on the PC (common for developers, who often have ANTHROPIC_API_KEY set for other tools).
Settings offers "Use my own key instead", which leaves the beta.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from pyxi import secretstore

log = logging.getLogger(__name__)

# Where the beta server lives (cloud/ in this repo, deployed on Vercel). Overridable for testing.
DEFAULT_URL = "https://pyxi-beta.vercel.app"
BETA_MODEL = "anthropic/claude-haiku-5.5"
# What the beta server lets through (cloud/lib/beta.ts DEFAULT_MODELS).
BETA_MODELS = ("anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5")
# Per-feature model overrides: in the beta, anything the server wouldn't allow falls back to the
# chat model (empty), so nothing quietly bills a key of their own.
MODEL_VARS = ("PYXI_JOB_MODEL", "PYXI_MODEL_3D", "PYXI_MODEL_HANDS", "PYXI_MODEL_HANDS_STRONG", "PYXI_MODEL_AIM")
FILE = "cloud.json"


def server_url() -> str:
    return os.environ.get("PYXI_CLOUD_URL", DEFAULT_URL).rstrip("/")


def saved(data_dir: Path) -> dict[str, Any] | None:
    d = secretstore.read_json(data_dir / FILE)  # an old plain file is encrypted on first read
    return d if d is not None and str(d.get("token", "")).startswith("pyxi_") else None


def has_own_key() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def had_own_key() -> bool:
    """Whether this PC had a key of its own before the beta took over (apply() records it,
    because joining replaces OPENROUTER_API_KEY with the beta token)."""
    flag = os.environ.get("PYXI_OWN_KEY")
    return flag == "1" if flag is not None else has_own_key()


def apply(data_dir: Path) -> bool:
    """Before settings load: with a saved beta token, route every model call through the beta
    server, even when a key of their own is set. Returns whether the beta is in use."""
    os.environ["PYXI_OWN_KEY"] = "1" if has_own_key() else "0"
    beta = saved(data_dir)
    if beta is None:
        return False
    url = str(beta.get("url") or server_url()).rstrip("/")
    os.environ["OPENROUTER_API_KEY"] = beta["token"]
    os.environ["PYXI_OPENROUTER_URL"] = url + "/api/v1"
    if os.environ.get("PYXI_MODEL") not in BETA_MODELS:
        os.environ["PYXI_MODEL"] = BETA_MODEL
    for var in MODEL_VARS:
        if os.environ.get(var) and os.environ[var] not in BETA_MODELS:
            os.environ[var] = ""
    os.environ["PYXI_BETA"] = "1"
    return True


def leave(data_dir: Path) -> bool:
    """Forget the beta token, so the next start uses their own key again."""
    try:
        (data_dir / FILE).unlink()
    except FileNotFoundError:
        return False
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
    secretstore.write_json(data_dir / FILE, {"url": url, "token": body["token"]})
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
