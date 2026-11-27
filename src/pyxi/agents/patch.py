"""Patch, code: an error explained, the line found, and a small fix applied only after a card.

* **Where it broke.** A stack trace or compiler message (Python, Node/JavaScript/TypeScript,
  C#/MSBuild, Rust, Go, Java) is read for file-and-line pairs; the deepest one that is a real
  file on this PC (not inside site-packages or node_modules) is the line to look at.
* **One small model call.** The file (or 120 lines around the line, for big files) and the
  error go to the model, which explains it in plain words and proposes the smallest fix as an
  exact find-and-replace. The fix is checked here before anyone sees it: the text must be in
  the file exactly once, only a few lines may change, and Python must still parse.
* **Nothing is written without the card.** The brain shows the diff on an approval card
  (``companion/specialists.py``). On Allow the file is changed and a backup is kept beside it
  (``name.py.bak``); on No nothing changes.
"""

from __future__ import annotations

import ast
import difflib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyxi import safety

if TYPE_CHECKING:
    from pyxi.agents.jobs import Job, JobManager
    from pyxi.agents.registry import AgentProfile

CODE = {
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".cs", ".java", ".go",
    ".rs", ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cc", ".sh", ".ps1", ".bat", ".cmd", ".sql",
    ".html", ".css", ".scss", ".yaml", ".yml", ".toml", ".ini", ".lua", ".r", ".swift", ".kt",
}  # fmt: skip
MAX_BYTES = 600_000
MAX_CHANGED = 12  # lines: a small fix, not a rewrite
WINDOW = 60  # lines either side of the error in a big file
WHOLE_CHARS = 60_000  # a big file with no line to go by: this much of it, from the top
BOM = chr(0xFEFF)  # the byte-order mark some Windows editors put at the start of a file

# file, line pairs in the ways tools print them
LOCATIONS = [
    re.compile(r'File "(?P<f>[^"]+)", line (?P<l>\d+)'),  # Python
    re.compile(
        r"\((?P<f>(?:[A-Za-z]:)?[^()\n]+?\.[A-Za-z0-9]{1,5}):(?P<l>\d+):\d+\)"
    ),  # node, with spaces
    re.compile(
        r"\(?(?P<f>(?:[A-Za-z]:)?[\\/]?[^\s():'\"]+\.[A-Za-z0-9]{1,5}):(?P<l>\d+)(?::\d+)?\)?"
    ),  # node, go, rust, gcc
    re.compile(r"(?P<f>(?:[A-Za-z]:)?[^\s():'\"]+\.[A-Za-z0-9]{1,5})\((?P<l>\d+),\d+\)"),  # tsc, C#
    re.compile(r"at [\w.$<>]+\((?P<f>[\w$]+\.java):(?P<l>\d+)\)"),  # Java (file name only)
]
NOT_THEIRS = re.compile(
    r"site-packages|dist-packages|node_modules|[\\/]lib[\\/]python\d|<frozen|<string>|\binternal[\\/]",
    re.I,
)

SYSTEM = """You fix one bug in a local script. Reply with JSON only:
{"explain": "what the error means and why it happens here, 2-3 short plain sentences",
 "line": <the line number where it goes wrong>,
 "find": "exact text from the file to replace, whole lines, copied character for character",
 "replace": "the corrected text", "why": "what the fix changes, one sentence"}
Rules: the smallest fix that makes the error go away for the right reason; keep the style,
indentation and names. `find` must appear exactly once in the file. If the error isn't in this
file, or a real fix needs more than a few lines or a decision only they can make, give
"find": "" and say what they should do in "explain". Plain words, no em dashes."""


@dataclass
class Location:
    path: str
    line: int


@dataclass
class Fix:
    path: str
    line: int
    explain: str
    find: str
    replace: str
    why: str = ""
    diff: str = ""

    def apply_to(self, text: str) -> str:
        return text.replace(self.find, self.replace, 1)


def locations(error: str, cwd: Path | None = None) -> list[Location]:
    """File-and-line pairs in an error, deepest (most recent call) first, real files only."""
    seen, out = set(), []
    for pat in LOCATIONS:
        for m in pat.finditer(error):
            raw, line = m.group("f").strip(), int(m.group("l"))
            if NOT_THEIRS.search(raw):
                continue
            p = Path(raw)
            if not p.is_absolute():
                if cwd is None:  # relative to Pyxi's own folder is never their script
                    continue
                p = cwd / p
            try:
                p = p.resolve()
            except OSError:
                continue
            key = (str(p).lower(), line)
            if key in seen or not p.is_file() or p.suffix.lower() not in CODE:
                continue
            seen.add(key)
            out.append((m.start(), Location(str(p), line)))
    # Python prints the most recent call last; node, tsc and Java print it first.
    py = bool(re.search(r"Traceback \(most recent call last\)", error))
    ordered = sorted(out, key=lambda t: t[0], reverse=py)
    return [loc for _, loc in ordered]


def as_line(value: Any) -> int:
    """A line number however it was written: 12, "12", "line 12", "12-14" (the first). 0 when
    there is none ("unknown" used to fail the whole job with int()'s own words)."""
    m = re.search(r"\d+", str(value if value is not None else ""))
    return int(m.group(0)) if m else 0


def line_in(error: str, path: Path) -> int:
    """The line the error names for this file (the deepest one), or 0."""
    try:
        want = str(Path(path).resolve()).lower()
    except OSError:
        return 0
    for loc in locations(error, cwd=Path(path).parent):
        if loc.path.lower() == want:
            return loc.line
    return 0


def excerpt(text: str, line: int, window: int = WINDOW) -> tuple[str, int]:
    """Numbered lines: the whole file if it's small, else ``window`` lines around ``line``.
    With no line to go by (``line`` 0), the whole file up to ``WHOLE_CHARS``: the first
    lines of a long script are rarely where it broke."""
    lines = text.splitlines()
    if len(lines) <= 2 * window + 40:
        lo, hi = 1, len(lines)
    elif line <= 0:
        lo, hi, size = 1, 0, 0
        while hi < len(lines) and size + len(lines[hi]) + 8 <= WHOLE_CHARS:
            size += len(lines[hi]) + 8
            hi += 1
    else:
        lo, hi = max(1, line - window), min(len(lines), line + window)
    return "\n".join(f"{i:>5}| {lines[i - 1]}" for i in range(lo, hi + 1)), lo


def parse(raw: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def clean(text: Any) -> str:
    return re.sub(r"\s*[—–]\s*", ", ", str(text or "")).strip()


def check(path: Path, text: str, find: str, replace: str) -> str | None:
    """Why this fix can't be offered, or None if it's fine."""
    if not find:
        return "no fix offered"
    n = text.count(find)
    if n == 0:
        # The model sometimes drops trailing spaces or changes line endings: try those.
        return "the text to replace isn't in the file"
    if n > 1:
        return "the text to replace appears more than once"
    if find == replace:
        return "the fix doesn't change anything"
    changed = sum(
        1 for d in difflib.ndiff(find.splitlines(), replace.splitlines()) if d[:1] in "+-"
    )
    if changed > MAX_CHANGED * 2:
        return "that's more than a small fix"
    # Without the byte-order mark Notepad, Visual Studio and PowerShell put first: as text
    # it is a syntax error to Python and to JSON, so every fix to such a file was refused.
    new = text.replace(find, replace, 1).lstrip(BOM)
    if path.suffix.lower() in (".py", ".pyw"):
        try:
            ast.parse(new)
        except SyntaxError as exc:
            return f"the fixed file wouldn't run (syntax error on line {exc.lineno})"
    if path.suffix.lower() == ".json":
        try:
            json.loads(new)
        except ValueError:
            return "the fixed file isn't valid JSON"
    return None


def normalise(text: str, find: str) -> str:
    """The model's ``find`` as it really is in the file: the same lines with the file's own
    trailing spaces, when only whitespace at the line ends differs."""
    if find in text:
        return find
    want = [ln.rstrip() for ln in find.splitlines()]
    lines = text.splitlines()
    for i in range(len(lines) - len(want) + 1):
        if [ln.rstrip() for ln in lines[i : i + len(want)]] == want:
            return "\n".join(lines[i : i + len(want)])
    return find


def make_diff(path: Path, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=path.name,
            tofile=f"{path.name} (fixed)",
            n=2,
        )
    )


def decode(data: bytes) -> tuple[str, str]:
    """A script's text and its encoding: UTF-8, or the Windows code page older editors save."""
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def apply(fix: Fix) -> Path:
    """Write the fix, keeping a backup beside the file. Refuses if the file changed since."""
    path = Path(fix.path)
    data = path.read_bytes()
    text, encoding = decode(data)
    # Every line keeps the ending it has: the text to replace is looked for with either
    # ending, and only those lines are written anew, with the ending they had (a file with
    # both kinds used to come back with every line CRLF).
    pattern = re.escape(fix.find.replace("\r\n", "\n")).replace("\\\n", "\r?\n")
    found = list(re.finditer(pattern, text))
    if len(found) != 1:
        raise RuntimeError(f"{path.name} changed since the fix was made. Ask me again.")
    old, after = found[0].group(0), text[found[0].end() : found[0].end() + 2]
    if "\n" in old:
        ending = "\r\n" if "\r\n" in old else "\n"
    elif after[:1] in ("\r", "\n"):
        ending = "\r\n" if after == "\r\n" else "\n"
    else:
        ending = "\r\n" if text.count("\r\n") * 2 > text.count("\n") else "\n"
    put = fix.replace.replace("\r\n", "\n").replace("\n", ending)
    new = text[: found[0].start()] + put + text[found[0].end() :]
    backup = path.with_name(path.name + ".bak")
    n = 2
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak{n}")
        n += 1
    try:
        out = new.encode(encoding)
    except UnicodeEncodeError:
        raise RuntimeError(f"The fix has characters {path.name} can't hold.") from None
    backup.write_bytes(data)
    try:
        path.write_bytes(out)
    except OSError:  # read-only, or locked by an editor: no stray backup, and plain words
        backup.unlink(missing_ok=True)
        raise RuntimeError(
            f"I couldn't save {path.name}: it's read-only or open in another program."
        ) from None
    return backup


# ------------------------------------------------------------------------------ the job
async def run(manager: JobManager, job: Job, agent: AgentProfile) -> None:
    from otto.llm.base import Message

    error = str(job.params.get("error") or job.context or "").strip()
    loc = Location(job.files[0], as_line(job.params.get("line"))) if job.files else None
    if loc is not None and not loc.line:  # the file was named, the line is in the error
        loc.line = line_in(error, Path(loc.path))
    if loc is None:
        found = locations(error)
        loc = found[0] if found else None
    if loc is None:
        raise RuntimeError("I couldn't tell which file the error is in. Tell me the script's path.")
    path = Path(loc.path)
    if not path.is_file():
        raise RuntimeError(f"I can't find {path.name} on this PC.")
    if path.suffix.lower() not in CODE or path.stat().st_size > MAX_BYTES:
        raise RuntimeError(f"{path.name} isn't a script I can safely edit.")
    raw = decode(path.read_bytes())[0].replace("\r\n", "\n")
    numbered, _ = excerpt(raw, loc.line)
    shown = numbered.count("\n") + 1 if numbered else 0
    cut = not loc.line and shown < len(raw.splitlines())
    await manager._status(
        job, f"Reading {path.name}" + (f" around line {loc.line}" if loc.line else "")
    )
    # The error is read off the screen or pasted in: it is data too, and fenced like the
    # file (a line in it "to the assistant" went into the prompt as if they had said it).
    said = (
        safety.fence("the error text", error[-4000:])
        if error
        else f"(they only said: {job.task})"
    )
    prompt = (
        f"The error:\n{said}\n\n"
        f"File {path.name}"
        + (f", the error points at line {loc.line}" if loc.line else "")
        + (f", its first {shown} lines (it is longer)" if cut else "")
        + ":\n"
        + safety.fence(path.name, numbered)  # a comment in the code is data, never orders
    )
    if job.params.get("ask"):
        prompt += f"\n\nWhat they asked: {job.params['ask']}"
    llm = manager.llm_factory(agent)
    resp = await llm.complete(
        system=SYSTEM + safety.READ_ONLY_RULE, messages=[Message.user(prompt)], max_tokens=2000
    )
    job.cost_usd = resp.usage.cost_usd
    data = parse(resp.text)
    if data is None:
        raise RuntimeError("The fix didn't come out right. Try again in a moment.")
    line = as_line(data.get("line")) or loc.line
    explain = clean(data.get("explain"))
    find = normalise(raw, str(data.get("find") or ""))
    replace = str(data.get("replace") or "")
    problem = check(path, raw, find, replace)
    where = f"line {line} of {path.name}" if line else path.name
    if problem:
        job.summary = f"{explain} No fix to apply ({problem})."
        job.data = {
            "title": f"The error is at {where}"[:70],
            "body": explain[:300],
            "changes": [],
            "location": asdict(Location(str(path), line)),
        }
        job.status = "done"
        return
    fix = Fix(str(path), line, explain, find, replace, clean(data.get("why")))
    fix.diff = make_diff(path, raw, fix.apply_to(raw))
    job.summary = f"{explain} Proposed fix at {where}: {fix.why} (waiting for their OK on the card)"
    job.data = {
        "title": f"Found it: {where}"[:70],
        "body": " ".join(re.split(r"(?<=[.!?])\s+", explain)[:2])[:200]
        + " Check the fix on the card; nothing changes until you allow it.",
        "changes": [fix.why] if fix.why else [],
        "card": {
            "title": f"{path.name}, line {line}",
            "sections": [
                {"heading": "What's wrong", "lines": [explain]},
                {
                    "heading": "The fix",
                    "lines": [ln for ln in fix.diff.splitlines()[2:] if ln.strip()][:16],
                },
            ],
        },
        "fix": asdict(fix),
        "location": asdict(Location(str(path), line)),
    }
    job.status = "done"
