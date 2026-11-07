"""Apps Pyxi never looks at: password managers, banking, sign-in and UAC prompts, the lock
screen, and whatever the user adds in Settings.

The built-in list is regexes on the window title and process name (config.DEFAULT_EXCLUDED);
the user's own are plain names ("Quicken", "my-vpn.exe"), kept in ``private_apps.json`` in the
data folder and matched as words. A private window in front is not captured at all (Pyxi says
it can't see it); one that's merely visible elsewhere on screen is blacked out of the
screenshot before anything reads it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FILE = "private_apps.json"

# What the built-in list covers, in words, for Settings.
BUILT_IN = [
    "Password managers: 1Password, Bitwarden, KeePass, LastPass, Dashlane",
    "Banking apps and pages with 'bank' or 'banking' in the title",
    "Windows Security, credential and sign-in prompts",
    "The Windows lock screen and sign-in screen",
    "User Account Control (\"Do you want to allow this app...\") prompts",
]


def load(data_dir: Path) -> list[str]:
    try:
        names = json.loads((Path(data_dir) / FILE).read_text(encoding="utf-8")).get("apps", [])
    except (OSError, ValueError, AttributeError):
        return []
    return clean(names)


def clean(names: object) -> list[str]:
    out: list[str] = []
    for n in names if isinstance(names, list) else []:
        name = " ".join(str(n).split())[:80]
        if len(name) >= 2 and name.lower() not in (o.lower() for o in out):
            out.append(name)
    return out[:100]


def save(data_dir: Path, names: object) -> list[str]:
    apps = clean(names)
    path = Path(data_dir) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"apps": apps}, indent=1), encoding="utf-8")
    return apps


def patterns(names: list[str]) -> list[str]:
    """Plain names as regexes: whole words, any case ("Quicken" doesn't match "quickenings")."""
    return [rf"(?<![\w]){re.escape(n)}(?![\w])" for n in names]
