"""The Mac side of ``pyxi.files_access.search_index``: Spotlight, through ``mdfind``.

Like the Windows Search index it knows names and contents and answers at once. Only the folders
the user allowed are searched (``-onlyin`` each). macOS asks the user the first time Pyxi looks in
Documents, Desktop or Downloads; until they allow it those folders return nothing.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = ["search_index"]


def spotlight_query(words: list[str]) -> str:
    """Every word in the name or the contents. ``_words`` keeps only letters, digits, dots and
    dashes, so nothing here can break out of the quotes."""
    return " && ".join(
        f'(kMDItemFSName == "*{w}*"cd || kMDItemTextContent == "{w}*"cdw)' for w in words
    )


def search_index(query: str, folders: list[str], limit: int = 20):
    from pyxi.files_access import Hit, _words

    words = _words(query)
    if not words:
        return []
    hits = []
    seen: set[str] = set()
    try:
        for folder in folders:
            if not Path(folder).is_dir():
                continue
            out = subprocess.run(
                ["mdfind", "-onlyin", folder, spotlight_query(words)],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if out.returncode != 0:
                return None
            for line in out.stdout.splitlines():
                p = Path(line.strip())
                if not line.strip() or str(p) in seen or not p.is_file():
                    continue
                seen.add(str(p))
                st = p.stat()
                hits.append(Hit(str(p), st.st_size, st.st_mtime))
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Spotlight search failed: %s", exc)
        return None
    hits.sort(key=lambda h: -h.modified)  # newest first, as on Windows
    return hits[:limit]
