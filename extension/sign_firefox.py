"""Sign the Firefox build with Mozilla, so that Pyxi can host it itself.

Firefox only keeps an add-on that Mozilla has signed. It doesn't have to be listed on
addons.mozilla.org for that: an "unlisted" add-on is checked by Mozilla's automatic review,
signed, and handed back, and from then on anyone can install it from wherever it is hosted.

    set WEB_EXT_API_KEY=<your JWT issuer>
    set WEB_EXT_API_SECRET=<your JWT secret>
    python extension/sign_firefox.py

(In PowerShell: ``$env:WEB_EXT_API_KEY = "..."``.) Both come from addons.mozilla.org, Developer
Hub, "Manage API Keys". They are read from the environment of the terminal this runs in, used
to sign each request to Mozilla, and nothing else: never printed, never written to a file,
never passed to another program.

What it leaves, in ``extension/signed/``:

    pyxi-firefox.xpi             the signed add-on
    pyxi-firefox-updates.json    what installed copies of the add-on read to find a new version

``scripts/release.py`` uploads both with every release, so that
https://github.com/marzukchowdhury/Pyxi/releases/latest/download/pyxi-firefox.xpi is the
add-on and .../pyxi-firefox-updates.json is its update file (the address the add-on's manifest
carries as ``update_url``).

    python extension/sign_firefox.py --check    builds the package and checks the two
                                                variables are set, without contacting Mozilla

A version can only be signed once. To sign changed code, raise "version" in manifest.json.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build import manifest, package  # noqa: E402

KEY_VAR, SECRET_VAR = "WEB_EXT_API_KEY", "WEB_EXT_API_SECRET"  # the names Mozilla's own tool uses
AMO = "https://addons.mozilla.org/api/v5/"
REPO = "marzukchowdhury/Pyxi"
LATEST = f"https://github.com/{REPO}/releases/latest/download"
XPI_NAME = "pyxi-firefox.xpi"
UPDATES_NAME = "pyxi-firefox-updates.json"
SIGNED = HERE / "signed"


def credentials(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """The API key and secret, from the environment only. Says which one is missing, and never
    what either of them is."""
    env = os.environ if env is None else env
    key, secret = env.get(KEY_VAR, "").strip(), env.get(SECRET_VAR, "").strip()
    missing = [name for name, value in ((KEY_VAR, key), (SECRET_VAR, secret)) if not value]
    if missing:
        raise SystemExit(
            f"STOP: {' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set in "
            "this terminal. Create them at addons.mozilla.org (Developer Hub, Manage API Keys), "
            "set them here, and run this again. See extension/README.md."
        )
    return key, secret


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def token(key: str, secret: str, now: float | None = None) -> str:
    """A short-lived JWT for one request to Mozilla (HS256, as their API asks): good for a
    minute, never reused."""
    now = int(time.time() if now is None else now)
    head = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"iss": key, "jti": secrets.token_hex(16), "iat": now, "exp": now + 60}
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    mac = hmac.new(secret.encode("utf-8"), f"{head}.{body}".encode("ascii"), hashlib.sha256)
    return f"{head}.{body}.{_b64url(mac.digest())}"


def updates(version: str, xpi: bytes) -> dict[str, Any]:
    """The update file: which version is current, where it is, and its hash (so that a file
    swapped at that address is not installed)."""
    gecko = manifest("firefox")["browser_specific_settings"]["gecko"]
    return {
        "addons": {
            gecko["id"]: {
                "updates": [
                    {
                        "version": version,
                        "update_link": f"{LATEST}/{XPI_NAME}",
                        "update_hash": "sha256:" + hashlib.sha256(xpi).hexdigest(),
                        "applications": {
                            "gecko": {"strict_min_version": gecko["strict_min_version"]}
                        },
                    }
                ]
            }
        }
    }


class Mozilla:
    """The four things asked of addons.mozilla.org: take the package, check it, sign it as a
    version of the add-on, and give the signed file back."""

    def __init__(
        self,
        key: str,
        secret: str,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        say: Callable[[str], None] = print,
    ) -> None:
        self._key, self._secret = key, secret
        self.client = client or httpx.Client(timeout=120, follow_redirects=True)
        self.sleep, self.say = sleep, say

    def _ask(self, method: str, url: str, **kw: Any) -> httpx.Response:
        headers = {"Authorization": f"JWT {token(self._key, self._secret)}"}
        try:
            r = self.client.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise SystemExit(f"STOP: couldn't reach Mozilla ({type(exc).__name__}).") from None
        if r.status_code in (401, 403):
            raise SystemExit(
                f"STOP: Mozilla refused the API key ({r.status_code}). Check {KEY_VAR} and "
                f"{SECRET_VAR}, and that this PC's clock is right."
            )
        if r.status_code == 409 or (r.status_code == 400 and "already exists" in r.text):
            raise SystemExit(
                "STOP: Mozilla already has this version. A version is signed once: raise "
                '"version" in extension/manifest.json and run this again.'
            )
        if r.status_code >= 400:
            raise SystemExit(f"STOP: Mozilla answered {r.status_code}: {r.text[:300]}")
        return r

    def upload(self, package_file: Path) -> str:
        with package_file.open("rb") as f:
            r = self._ask(
                "POST",
                AMO + "addons/upload/",
                data={"channel": "unlisted"},
                files={"upload": (package_file.name, f, "application/zip")},
            )
        return str(r.json()["uuid"])

    def validated(self, uuid: str, timeout: float) -> None:
        end = time.monotonic() + timeout
        while True:
            state = self._ask("GET", AMO + f"addons/upload/{uuid}/").json()
            if state.get("processed"):
                break
            if time.monotonic() >= end:
                raise SystemExit("STOP: Mozilla is still checking the package. Try again later.")
            self.sleep(2)
        if not state.get("valid"):
            problems = [
                str(m.get("message") or m)[:200]
                for m in (state.get("validation") or {}).get("messages", [])
                if not isinstance(m, dict) or m.get("type") == "error"
            ]
            raise SystemExit("STOP: Mozilla's check found problems:\n  " + "\n  ".join(problems or ["(none listed)"]))

    def submit(self, addon_id: str, uuid: str) -> str:
        """This upload as a version of the add-on (which is created if it is its first)."""
        r = self._ask("PUT", AMO + f"addons/addon/{addon_id}/", json={"version": {"upload": uuid}})
        return str(r.json()["version"]["id"])

    def signed(self, addon_id: str, version_id: str, timeout: float) -> str:
        """Wait for Mozilla's review (automatic for unlisted add-ons) and return where the
        signed file is."""
        end = time.monotonic() + timeout
        while True:
            file = self._ask("GET", AMO + f"addons/addon/{addon_id}/versions/{version_id}/").json().get("file") or {}
            if file.get("status") == "public" and file.get("url"):
                return str(file["url"])
            if file.get("status") == "disabled":
                raise SystemExit("STOP: Mozilla's review turned this version down. Its email says why.")
            if time.monotonic() >= end:
                raise SystemExit(
                    "STOP: Mozilla hasn't signed it yet. It is in their queue: run this again "
                    "later (the same version carries on where it was)."
                )
            self.sleep(5)

    def download(self, url: str) -> bytes:
        return self._ask("GET", url).content


def check_signed(xpi: bytes, addon_id: str, version: str) -> None:
    """What came back is this add-on, this version, and carries Mozilla's signature."""
    try:
        with zipfile.ZipFile(io.BytesIO(xpi)) as z:
            names = set(z.namelist())
            inside = json.loads(z.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise SystemExit("STOP: what Mozilla sent back isn't an add-on file.") from None
    if not any(n.startswith("META-INF/") and n.endswith((".rsa", ".sf")) for n in names):
        raise SystemExit("STOP: the file Mozilla sent back isn't signed.")
    gecko = inside.get("browser_specific_settings", {}).get("gecko", {})
    if gecko.get("id") != addon_id or inside.get("version") != version:
        raise SystemExit("STOP: the signed file isn't this add-on and version.")


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(description="Sign the Firefox build with Mozilla (unlisted).")
    parser.add_argument("--out", type=Path, default=SIGNED, help="where the signed files go")
    parser.add_argument("--timeout", type=float, default=900, help="seconds to wait for Mozilla")
    parser.add_argument("--check", action="store_true", help="build and check, sign nothing")
    args = parser.parse_args(argv)

    spec = manifest("firefox")
    addon_id = spec["browser_specific_settings"]["gecko"]["id"]
    version = spec["version"]
    key, secret = credentials(env)
    with tempfile.TemporaryDirectory(prefix="pyxi-firefox-") as tmp:
        unsigned = package("firefox", Path(tmp) / f"pyxi-firefox-{version}.zip")
        print(f"built {addon_id} {version} ({unsigned.stat().st_size // 1024} KB)")
        if args.check:
            print("the API key and secret are set, and the package builds. Nothing was sent.")
            return 0
        mozilla = Mozilla(key, secret, client, sleep)
        print("sending it to Mozilla...")
        uuid = mozilla.upload(unsigned)
        mozilla.validated(uuid, args.timeout)
        print("Mozilla's check passed. Waiting for the signature (usually a minute or two)...")
        version_id = mozilla.submit(addon_id, uuid)
        xpi = mozilla.download(mozilla.signed(addon_id, version_id, args.timeout))
    check_signed(xpi, addon_id, version)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / XPI_NAME).write_bytes(xpi)
    (out / UPDATES_NAME).write_text(json.dumps(updates(version, xpi), indent=2) + "\n", encoding="utf-8")
    print(f"signed: {out / XPI_NAME}")
    print(f"        {out / UPDATES_NAME}")
    print("Keep both files (commit them): scripts/release.py uploads them with every release.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
