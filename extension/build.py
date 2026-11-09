"""Make a loadable copy of the extension for one browser.

    python extension/build.py chrome     -> extension/dist/chrome   (Chrome and Edge)
    python extension/build.py firefox    -> extension/dist/firefox  (Firefox 128 or newer)

Chrome and Edge can also load this folder as it is (it holds the Chrome manifest). Firefox
needs its own manifest: the same code, with an event page instead of a service worker and an
add-on id (``manifest.firefox.json`` is laid over ``manifest.json``).

``--test-host 127.0.0.1`` is for the end-to-end check only (scripts/browser_e2e.py): it gives
the copy standing access to that one host, because a test can't click the extension's icon
the way a person does. Never ship a copy built with it.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHIPPED = ["background.js", "content.js", "popup.html", "popup.css", "popup.js", "icons"]


def manifest(target: str, test_host: str = "") -> dict:
    data = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    if target == "firefox":
        overlay = json.loads((HERE / "manifest.firefox.json").read_text(encoding="utf-8"))
        data.pop("minimum_chrome_version", None)
        data.update(overlay)  # "background" is replaced whole: scripts, no service_worker
    if test_host:
        data["host_permissions"] = [f"http://{test_host}/*"]
        data["name"] += " (test build)"
    return data


def build(target: str, out: Path | None = None, test_host: str = "") -> Path:
    if target not in ("chrome", "firefox"):
        raise SystemExit(f"unknown target {target!r}: chrome or firefox")
    out = Path(out) if out else HERE / "dist" / target
    if out.exists() and any(out.iterdir()):
        try:  # replace an earlier copy this script made, and nothing else
            ours = json.loads((out / "manifest.json").read_text(encoding="utf-8"))["name"]
        except (OSError, ValueError, KeyError):
            ours = ""
        if not str(ours).startswith("Pyxi in your browser"):
            raise SystemExit(f"{out} isn't an earlier build of this extension: not touching it")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    for name in SHIPPED:
        src = HERE / name
        if src.is_dir():
            shutil.copytree(src, out / name)
        else:
            shutil.copy2(src, out / name)
    (out / "manifest.json").write_text(
        json.dumps(manifest(target, test_host), indent=2) + "\n", encoding="utf-8"
    )
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", choices=["chrome", "firefox"])
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--test-host", default="", help="end-to-end checks only")
    args = parser.parse_args()
    print(build(args.target, args.out, args.test_host))
