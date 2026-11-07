"""Opt-in file access: "Let Pyxi search my files".

Off until the user turns it on (a consent card the first time Pyxi wants to look, or the switch
in Settings). It only ever looks inside the folders they allow (Documents, Desktop, Downloads
and Pictures to start with). The choice lives in the data dir as ``file_access.json``.

Search asks the Windows Search index first (instant, and it knows file contents), and falls
back to a quick walk of the allowed folders when the index isn't there or has nothing. Reading
gives the model plain text: text files as they are, Word, PDF, Excel and PowerPoint outlined.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

FILE = "file_access.json"
SKIP_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "$recycle.bin",
    "appdata",
    ".cache",
    "site-packages",
}
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".html", ".htm", ".yaml", ".yml",
    ".ini", ".py", ".js", ".ts", ".tsx", ".css", ".sql", ".rtf",
}  # fmt: skip


def default_folders() -> list[str]:
    home = Path.home()
    return [str(home / name) for name in ("Documents", "Desktop", "Downloads", "Pictures")]


@dataclass
class FileAccess:
    enabled: bool = False
    folders: list[str] = field(default_factory=lambda: default_folders())
    asked: bool = False  # the consent card has been shown (so a "no" sticks)

    def public(self) -> dict:
        return asdict(self)


def load(data_dir: Path) -> FileAccess:
    try:
        raw = json.loads((Path(data_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FileAccess()
    folders = [str(f) for f in raw.get("folders") or [] if str(f).strip()]
    return FileAccess(bool(raw.get("enabled")), folders, bool(raw.get("asked")))


def save(data_dir: Path, access: FileAccess) -> FileAccess:
    clean, seen = [], set()
    for f in access.folders:
        p = str(Path(str(f).strip().strip('"')).expanduser())
        if p and p.lower() not in seen:
            seen.add(p.lower())
            clean.append(p)
    access.folders = clean[:20]
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    (Path(data_dir) / FILE).write_text(json.dumps(access.public(), indent=2), encoding="utf-8")
    return access


def allowed(path: Path, access: FileAccess) -> bool:
    """Inside one of the allowed folders (after resolving .. and links)."""
    try:
        real = Path(path).resolve()
    except OSError:
        return False
    for folder in access.folders:
        try:
            real.relative_to(Path(folder).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


# ---------------------------------------------------------------------------- search
@dataclass
class Hit:
    path: str
    size: int
    modified: float

    def line(self) -> str:
        when = time.strftime("%Y-%m-%d", time.localtime(self.modified))
        kb = max(1, self.size // 1024)
        return f"{self.path} ({kb:,} KB, changed {when})"


def _words(query: str) -> list[str]:
    return [w for w in re.findall(r"[\w.-]+", query.lower()) if len(w) > 1][:6]


def _kinds(kind: str) -> set[str]:
    return {
        "pdf": {".pdf"},
        "image": {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".bmp", ".tif", ".tiff"},
        "word": {".docx", ".doc", ".odt", ".rtf"},
        "excel": {".xlsx", ".xlsm", ".xls", ".csv", ".ods"},
        "powerpoint": {".pptx", ".ppt", ".odp"},
        "text": TEXT_EXTS,
    }.get(kind.lower().strip(), set())


def search_index(query: str, folders: list[str], limit: int = 20) -> list[Hit] | None:
    """The Windows Search index (None when it isn't available). Matches names and contents."""
    if os.name != "nt":
        return None
    try:
        import comtypes.client  # comes with uiautomation
    except ImportError:
        return None
    words = _words(query)
    if not words:
        return []
    scopes = " OR ".join(
        "SCOPE='file:" + Path(f).as_posix().replace("'", "''") + "'" for f in folders
    )
    # _words keeps only letters, digits, dots and dashes, so nothing here can break the query.
    terms = " AND ".join(
        f"(System.FileName LIKE '%{w}%' OR CONTAINS(*, '\"{w}*\"'))" for w in words
    )
    sql = (
        f"SELECT TOP {int(limit)} System.ItemPathDisplay, System.Size, System.DateModified "
        f"FROM SystemIndex WHERE ({scopes}) AND ({terms}) AND System.FileAttributes <> 16 "
        "ORDER BY System.DateModified DESC"
    )
    try:
        conn = comtypes.client.CreateObject("ADODB.Connection")
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        try:
            rs = comtypes.client.CreateObject("ADODB.Recordset")
            rs.Open(sql, conn)
            hits = []
            while not rs.EOF:
                path = str(rs.Fields.Item(0).Value or "")
                if path and Path(path).is_file():
                    st = Path(path).stat()
                    hits.append(Hit(path, st.st_size, st.st_mtime))
                rs.MoveNext()
            rs.Close()
            return hits
        finally:
            conn.Close()
    except Exception:
        return None


def search_walk(
    query: str,
    folders: list[str],
    limit: int = 20,
    budget_s: float = 3.0,
    kinds: set[str] | None = None,
) -> list[Hit]:
    """Walk the allowed folders for names containing every word (newest first)."""
    words = _words(query)
    deadline = time.monotonic() + budget_s
    hits: list[Hit] = []
    for folder in folders:
        root = Path(folder)
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")
            ]
            for name in filenames:
                low = name.lower()
                if kinds and Path(low).suffix not in kinds:
                    continue
                if words and not all(w in low for w in words):
                    continue
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                hits.append(Hit(str(p), st.st_size, st.st_mtime))
            if time.monotonic() > deadline or len(hits) > limit * 10:
                break
        if time.monotonic() > deadline:
            break
    hits.sort(key=lambda h: h.modified, reverse=True)
    return hits[:limit]


def search(
    query: str, access: FileAccess, kind: str = "", limit: int = 15, use_index: bool = True
) -> list[Hit]:
    kinds = _kinds(kind) if kind else set()
    hits = search_index(query, access.folders, limit * 3) if use_index else None
    if hits:
        hits = [h for h in hits if allowed(Path(h.path), access)]
        if kinds:
            hits = [h for h in hits if Path(h.path).suffix.lower() in kinds]
    if not hits:  # no index, or it found nothing (new files, folders it doesn't cover)
        hits = search_walk(query, access.folders, limit, kinds=kinds or None)
    return hits[:limit]


# ---------------------------------------------------------------------------- read
def read_text(path: Path, max_chars: int = 8000) -> str:
    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        text = path.read_text(encoding="utf-8", errors="replace")
    elif ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages, 1):
            pages.append(f"[page {i}] {page.extract_text() or ''}")
            if sum(map(len, pages)) > max_chars:
                break
        text = f"PDF, {len(reader.pages)} pages.\n" + "\n".join(pages)
    elif ext in (".docx", ".xlsx", ".xlsm", ".pptx"):
        from pyxi.agents.ledger import open_local

        text = open_local(path).describe()
    elif ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
        from PIL import Image

        with Image.open(path) as img:
            text = f"An image, {img.width}x{img.height} px, {img.format}, {path.stat().st_size // 1024:,} KB."
    else:
        raise ValueError(f"I can't read {ext or 'that kind of'} files.")
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... (cut; {len(text):,} characters in all)"
    return text
