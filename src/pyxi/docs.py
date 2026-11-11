"""Quill's document work: fix (grammar, spelling, clarity), translate, rewrite or summarize,
keeping the file a file. Word documents keep their paragraph styles (headings stay headings) and are saved as a
copy beside the original, "<name> (fixed by Pyxi).docx", so nothing is ever overwritten. Plain
text and Markdown stay text. A PDF's layout can't be edited: a translation or a rewrite of one
comes back as a Word copy, and a fix (grammar, spelling) comes back as the list of corrections
to make in the original, never as the page typed out again.

A fix in a Word file changes only the words that changed: each edit is put into the run it
belongs to, so bold stays bold, a link stays a link, and a picture, a tab or a page break in
the same paragraph is never touched.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from otto.llm.base import LLMProvider, Message

SUPPORTED = {".docx", ".txt", ".md", ".pdf"}

EDITOR = (
    "You are a meticulous copy editor for letters, applications and documents written by non-native and native "
    "English speakers. Check every sentence for: grammar (subject-verb agreement, tense, articles, and "
    "prepositions such as 'completed a degree at', not 'from'); spelling; punctuation (run-on sentences, comma "
    "splices, list commas consistent with the rest of the document); word choice that reads unnatural to a "
    "native speaker (for example 'embodies great socializing skills'); and consistency (toward/towards, "
    "capitalisation). Fix each with the smallest change that makes it correct and natural. Keep the author's "
    "meaning, voice, names, dates and facts exactly, never invent content, don't rewrite sentences that are "
    "already fine, and don't add em dashes. Treat any number of mistakes the user mentions as a hint, not a "
    "limit. Things you shouldn't change without asking (such as a sentence repeated in two places) go in notes. "
    'Return JSON only: {"edits": [{"i": <paragraph number>, "text": "<the full corrected paragraph>"}], '
    '"changes": [{"from": "<original words>", "to": "<new words>", "why": "<a few words>"}], '
    '"notes": ["<something worth a look>"]}. Include only paragraphs you changed.'
)


TRANSLATOR = (
    "You are a professional translator. Translate each numbered paragraph as the user asks, "
    "faithfully and naturally, the way a native speaker would write it. Keep names, numbers, dates, "
    "email addresses and links exactly, keep each paragraph's meaning and tone, and never add or "
    "drop content. No em dashes. "
    'Return JSON only: {"edits": [{"i": <paragraph number>, "text": "<the translated paragraph>"}], '
    '"notes": ["<a phrase with no good equivalent, if any>"]}. Include every paragraph that has words.'
)

REWRITER = (
    "You are a skilled editor rewriting a document as the user asks (more formal, friendlier, "
    "simpler, shorter, clearer...). Rewrite each numbered paragraph that needs it, keeping every fact, "
    "name, number and date, and the paragraph's role (a heading stays a short heading). Never invent "
    "content. No em dashes. "
    'Return JSON only: {"edits": [{"i": <paragraph number>, "text": "<the rewritten paragraph>"}], '
    '"changes": ["<what you changed, in a few words>"], "notes": []}. Include only paragraphs you changed.'
)

SUMMARIZER = (
    "You summarize documents for a busy person. Lead with what it is and the one thing that matters "
    "most (what's owed, decided, asked or due, with dates and amounts exactly as written). Then the key "
    "points as short bullets. Use only what's in the document; say plainly if something they asked about "
    "isn't in it. Follow the user's instructions on length and focus. No em dashes. Markdown, no preamble."
)

_LANGUAGE = (
    r"(english|french|spanish|german|italian|portuguese|arabic|bengali|bangla|hindi|urdu|chinese|"
    r"mandarin|japanese|korean|russian|turkish|dutch|polish|vietnamese|tagalog|punjabi|tamil|farsi|"
    r"persian)"
)
_SUMMARY = r"(summary|gist|overview|digest|rundown|recap|key points|main points)"

# What a Quill document job does, from the words of the task. These ask for it outright (a
# verb): "summarize it", "give me the gist", "translate into French", "make it more formal".
MODES = {
    "summarize": r"\b(summari[sz]|sum (it |this |that |them )?up|tl;?dr|brief me|"
    r"(give|get|write|send|make|create|prepare|need|want|like)\s+(me\s+|us\s+)?(an?|the|some)\s+"
    rf"(\w+\s+){{0,2}}{_SUMMARY}\b)",
    "translate": rf"\b(translat|into {_LANGUAGE}\b|"
    rf"(put|turn|render|convert|write)\s+(it|this|that|them|everything)\s+(in|to)\s+{_LANGUAGE}\b)",
    "rewrite": r"\b(rewrite|re-write|rephrase|reword|simplif|shorten|make it (more|less|sound)|more (formal|"
    r"professional|friendly|casual|concise)|friendlier|plain english)",
}
# These only name the thing ("the summary", "in French", "the tone"). They choose the job when
# nothing in the task asks for a proofread, and never against one: "proofread the summary on
# my resume" and "check the grammar, it is written in French" are grammar checks.
HINTS = {
    "summarize": rf"\b{_SUMMARY}\b",
    "translate": rf"\bin {_LANGUAGE}\b",
    "rewrite": r"\btone\b",
}


# "Don't rewrite it", "without rephrasing", "keep the tone": the opposite of asking for one.
_NOT_THAT = re.compile(
    r"\b(don'?t|do not|never|no|not|nor|or|without|avoid|keep(ing)?|same|preserv\w*|leave)\b(?:\W+\w+){0,4}?\W+$"
)
_PROOFREAD = re.compile(
    r"\b(grammar|spelling|punctuation|typos?|proof-?read\w*|mistakes?|errors?|correct\w*)\b"
)

# A file's name is not an instruction: "Fix the grammar in Meeting Summary.docx" was summarized,
# and "Spanish Homework.docx" was translated.
_FILE_EXT = r"(?:docx?|pdf|txt|md|rtf|odt|xlsx?|pptx?|html?|csv)"
_FILE_PATH = re.compile(rf"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\|~/)[^\"'\n]*?\.{_FILE_EXT}\b", re.I)
_FILE_NAME = re.compile(rf".*\.{_FILE_EXT}", re.I)
_QUOTED = re.compile(
    r"\"[^\"\n]{1,120}\"|“[^”\n]{1,120}”|‘[^’\n]{1,120}’|(?<!\w)'[^'\n]{1,120}'(?!\w)"
)
_VERBS = {
    "summarize", "summarise", "translate", "rewrite", "rephrase", "reword", "simplify",
    "shorten", "proofread", "fix", "check", "correct", "edit",
}  # fmt: skip


def _without_names(task: str) -> str:
    """The task without the files it names: full paths, anything in quotes, and a name ending
    in a document extension together with the capitalised words that lead up to it."""
    task = _QUOTED.sub(" ", _FILE_PATH.sub(" ", task))
    words = task.split()
    drop: set[int] = set()
    for k, word in enumerate(words):
        if not _FILE_NAME.fullmatch(word.strip(".,;:!?()")):
            continue
        drop.add(k)
        j = k - 1
        # "Meeting Summary.docx": walk back over the name's other words. Never the first word
        # of the task or of a sentence, and never the verb itself ("Summarize Report.pdf").
        while (
            j > 0
            and (words[j][:1].isupper() or words[j][:1].isdigit() or words[j] in ("-", "&", "–"))
            and words[j].lower() not in _VERBS
            and not words[j - 1].endswith((".", "!", "?", ":", ";"))
        ):
            drop.add(j)
            j -= 1
    return " ".join(w for k, w in enumerate(words) if k not in drop)


def _asked(pattern: str, low: str) -> bool:
    return any(not _NOT_THAT.search(low[: m.start()]) for m in re.finditer(pattern, low))


def document_mode(task: str) -> str:
    """fix, translate, rewrite or summarize. A word that is only there to rule it out ("fix
    the grammar, don't rewrite anything") doesn't count. Live: a resume sent for a grammar
    check, "fix them only, keep the layout", was rewritten from top to bottom because the
    task named the thing it was told not to do. Nor does a file's name, or a noun beside a
    request to proofread ("fix the typos in the summary")."""
    low = _without_names(task).lower()
    proofread = bool(_PROOFREAD.search(low))
    order = ("summarize", "translate", "rewrite")
    for mode in order:
        if _asked(MODES[mode], low) and not (mode == "rewrite" and proofread and "only" in low):
            return mode
    if proofread:
        return "fix"
    return next((mode for mode in order if _asked(HINTS[mode], low)), "fix")


def _describe(change: object) -> str:
    if isinstance(change, dict):
        a, b, why = (str(change.get(k, "")).strip() for k in ("from", "to", "why"))
        if a or b:
            return f"'{a}' → '{b}'" + (f" ({why})" if why else "")
        return ""
    return str(change).strip()


CHUNK_CHARS = 6000


@dataclass
class FixResult:
    path: Path
    changed: int
    total: int
    changes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # worth a look, but not changed without asking
    cost_usd: float = 0.0
    listed: bool = False  # a PDF: its corrections are listed, nothing was typed out again


def _read(path: Path) -> tuple[list[str], object | None]:
    ext = path.suffix.lower()
    if ext == ".docx":
        from docx import Document

        from pyxi.agents.pixel_common import word_text

        doc = Document(str(path))
        paras = _word_paragraphs(doc)
        return [word_text(p) for p in paras], (doc, paras)
    if ext == ".pdf":
        # The same reader Pixel uses: a scanned page is read with OCR, and a locked or broken
        # PDF says so in a sentence. (Read plainly, a scan had no words at all, and the job
        # still ended "Your document is fixed".)
        from pyxi.agents.pixel_pdf import page_texts

        pages, _ = page_texts(path)
        blocks = [
            re.sub(r"\s*\n\s*", " ", b).strip() for page in pages for b in re.split(r"\n\s*\n", page)
        ]
        return [b for b in blocks if b], None
    from pyxi.agents.pixel_render import read_any_text

    # Notepad's "ANSI" and "Unicode" files are not UTF-8: read as if they were, a café came back
    # as "caf�" in the fixed copy.
    return read_any_text(path).replace("\r\n", "\n").replace("\r", "\n").split("\n"), None


def _docx_paragraphs(doc):
    yield from doc.paragraphs
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


def _word_paragraphs(doc) -> list[Any]:
    """Every paragraph (w:p) a reader of the document sees, in order: the body with its tables,
    tables inside tables, content controls (what Word's resume templates are made of) and text
    boxes, then each header and footer. ``doc.paragraphs`` has only the body's top level, so
    the rest was never proofread."""
    from pyxi.agents.pixel_common import W

    out: list[Any] = []
    seen: dict[int, Any] = {}  # by id, holding each one so its id stays its own

    def take(root: Any) -> None:
        for p in root.iter(W + "p"):
            if id(p) in seen:
                continue
            seen[id(p)] = p
            # A text box is stored twice, the second time for old versions of Word.
            if not any(a.tag == _MC_FALLBACK for a in p.iterancestors()):
                out.append(p)

    take(doc.element.body)
    for section in doc.sections:
        for part in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            # One that only repeats the section before has nothing of its own (and asking for
            # its content would add an empty one to the file).
            if not part.is_linked_to_previous:
                take(part._element)
    return out


# ---------------------------------------------------------------------------- Word: patching runs
@dataclass
class _Atom:
    """One stretch of a paragraph's text: the words of a w:t, or a tab or line break."""

    el: Any
    text: str
    start: int
    zone: Any  # the link, field or content control it is inside; None for ordinary text
    plain: bool  # words that can change (a tab or a break stays where it is)
    cuts: list[tuple[int, int, str]] = field(default_factory=list)

    @property
    def end(self) -> int:
        return self.start + len(self.text)


def _atoms(p: Any) -> list[_Atom]:
    from pyxi.agents.pixel_common import W, run_pieces, word_runs

    out: list[_Atom] = []
    at = 0
    fields: list[Any] = []  # the complex fields (a date, a page number) we are inside
    for r, zone in word_runs(p):
        for mark in r.iter(W + "fldChar"):
            kind = mark.get(W + "fldCharType")
            if kind == "begin":
                fields.append(mark)
            elif kind == "end" and fields:
                fields.pop()
        for el, text, plain in run_pieces(r):
            out.append(_Atom(el, text, at, zone if zone is not None else (fields or [None])[0], plain))
            at += len(text)
    return out


def _tokens(text: str) -> list[str]:
    return re.findall(r"\s+|\w+|[^\w\s]", text)


def _text_ops(old: str, new: str) -> list[tuple[int, int, str]]:
    """(start, end, replacement) for each stretch of ``old`` that differs in ``new``, by whole
    words. White space lines up with white space, so a tab the model typed as a space is still
    that tab, and only a doubled space is tidied."""
    a, b = _tokens(old), _tokens(new)
    starts = [0]
    for t in a:
        starts.append(starts[-1] + len(t))

    def key(t: str) -> str:
        return " " if t.isspace() else t

    def tidy(t: str) -> str:
        return re.sub(r"\s*[\t\n\r]\s*", " ", t)  # a tab or a new line can't be typed into a run

    ops: list[tuple[int, int, str]] = []
    matcher = difflib.SequenceMatcher(a=[key(t) for t in a], b=[key(t) for t in b], autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                was, now = a[i1 + k], b[j1 + k]
                if was != now and not re.search(r"[\t\n\r]", was + now):
                    ops.append((starts[i1 + k], starts[i1 + k + 1], now))
            continue
        ops.append((starts[i1], starts[i2], tidy("".join(b[j1:j2]))))
    return ops


def _spot(atoms: list[_Atom], old: str, a: int, b: int, text: str) -> tuple[_Atom, str] | None:
    """Where the new words of one change go: (the stretch, "in", "before" or "after" it)."""
    if a < b:
        # In place of old words: into the run the first of them was in, with its formatting.
        hit = next((x for x in atoms if x.plain and x.start < b and x.end > a), None)
        if hit is not None:
            return hit, "in"
    before = next((x for x in atoms if x.text and x.start < a <= x.end), None)
    after = next((x for x in atoms if x.text and x.start <= a < x.end), None)
    if before is None and after is None:
        return None
    if before is after:
        return (before, "in") if before.plain else None
    if before is None or after is None:
        pick, side = (after, "before") if before is None else (before, "after")
    elif (before.zone is None) != (after.zone is None):
        # At the edge of a link or a field: new words go outside it.
        pick, side = (before, "after") if before.zone is None else (after, "before")
    elif before.plain != after.plain:
        pick, side = (before, "after") if before.plain else (after, "before")
    elif old[a - 1 : a].isspace() and not text[:1].isspace():
        pick, side = after, "before"  # a new word after a space belongs with what follows
    else:
        pick, side = before, "after"
    return pick, ("in" if pick.plain else side)


def _patch(p: Any, new: str, *, keep_edges: bool = True) -> bool:
    """Make a Word paragraph read ``new`` by changing only the words that differ, each in the
    run it sits in. Nothing is removed but words: runs, pictures, tabs, breaks, links and
    fields stay. False (and nothing changed) when a change can't be placed safely: it runs
    across the edge of a link or a field, or there is no text to put it in.

    Live: a resume's "Skills:" label was bold and the rest wasn't; one spelling fix later the
    whole line was bold, because the new text all went into the first run."""
    from pyxi.agents.pixel_common import W

    atoms = _atoms(p)
    old = "".join(x.text for x in atoms)
    if keep_edges:  # the model is shown, and returns, the paragraph without its outer spaces
        new = old[: len(old) - len(old.lstrip())] + new.strip() + old[len(old.rstrip()) :]
    if new == old:
        return True
    plan: list[tuple[_Atom, str, int, str]] = []
    for a, b, text in _text_ops(old, new):
        cut = [x for x in atoms if x.plain and x.start < b and x.end > a]
        if len({id(x.zone) for x in cut}) > 1:
            return False  # half in a link (or a field), half out of it
        spot = _spot(atoms, old, a, b, text) if text else None
        if text and spot is None:
            return False
        for x in cut:
            x.cuts.append((max(a, x.start) - x.start, min(b, x.end) - x.start, ""))
        if spot is not None:
            plan.append((spot[0], spot[1], a, text))
    # Every change is known to fit: only now is the paragraph touched.
    space = "{http://www.w3.org/XML/1998/namespace}space"
    for x, side, a, text in plan:
        if side == "in":
            at = min(max(a - x.start, 0), len(x.text))
            x.cuts.append((at, at, text))
            continue
        fresh = x.el.makeelement(W + "t", {space: "preserve"})
        fresh.text = text
        (x.el.addnext if side == "after" else x.el.addprevious)(fresh)
    for x in atoms:
        if not x.cuts:
            continue
        text, shift = x.text, 0
        for a, b, put in sorted(x.cuts, key=lambda c: (c[0], c[1])):
            text = text[: a + shift] + put + text[b + shift :]
            shift += len(put) - (b - a)
        x.el.text = text
        x.el.set(space, "preserve")  # or Word drops a space at the edge of a run
    return True


def _set_text(paragraph, text: str) -> None:
    """Make a paragraph read ``text``, keeping each run's formatting wherever the change can be
    placed (see ``_patch``). When it can't, the old way: its style and its first run's
    formatting. Ledger's find and replace uses this too."""
    from pyxi.agents.pixel_common import W, word_runs, word_text

    old = paragraph.text
    same_breaks = re.findall(r"[\t\n]", old) == re.findall(r"[\t\n]", text)
    if word_text(paragraph._p) == old and same_breaks and _patch(paragraph._p, text, keep_edges=False):
        return
    # The words of a link are in the new text now: left in the link as well, they'd be there twice.
    for r, _ in word_runs(paragraph._p):
        if r.getparent() is not paragraph._p:
            for t in r.iter(W + "t"):
                t.text = ""
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return
    runs[0].text = text
    for r in runs[1:]:
        r._element.getparent().remove(r._element)


def _chunks(texts: list[str]) -> list[list[int]]:
    out: list[list[int]] = [[]]
    size = 0
    for i, t in enumerate(texts):
        if not t.strip():
            continue
        if size + len(t) > CHUNK_CHARS and out[-1]:
            out.append([])
            size = 0
        out[-1].append(i)
        size += len(t)
    return [c for c in out if c]


def _parse(text: str) -> dict | None:
    """The model's JSON, or None when there is none to read (cut off, or not JSON at all)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _numbers(text: str) -> list[str]:
    """The numbers in a piece of text, however they are grouped: "$1,200" and "1200" are the
    same one, "March 3" and "March 5" are not."""
    return sorted(re.findall(r"\d+", re.sub(r"(?<=\d)[,.](?=\d)", "", text)))


def _has_words(text: str) -> bool:
    return bool(re.search(r"[^\W\d_]{2,}", text))


def _diff_changes(old: str, new: str) -> list[str]:
    """What changed between two versions of a paragraph, as "'old words' → 'new words'"."""
    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op != "replace":  # a word added or dropped reads best beside its neighbour
            if i1 > 0:
                i1, j1 = i1 - 1, j1 - 1
            elif i2 < len(a) and j2 < len(b):
                i2, j2 = i2 + 1, j2 + 1
        out.append(f"'{' '.join(a[i1:i2])}' → '{' '.join(b[j1:j2])}'")
    return out


def _clip(text: str, n: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


async def fix_document(
    path: Path, instructions: str, llm: LLMProvider, *, mode: str = "fix"
) -> FixResult:
    """Fix (two passes: edit, then review), translate or rewrite (one pass) each paragraph, and
    save a copy beside the original: "<name> (fixed by Pyxi)", "(translated by Pyxi)"...

    What the model sends back is checked before anything is saved. An answer that can't be
    read is asked for again in two halves, then it is a failure, never a copy with nothing
    done to it. A translation that skips paragraphs is a failure. And a grammar fix that
    changes a number is not made: that paragraph stays as it was, with a note."""
    if path.suffix.lower() not in SUPPORTED:
        raise ValueError(f"I can work on {', '.join(sorted(SUPPORTED))} files, not {path.suffix}.")
    system = {"translate": TRANSLATOR, "rewrite": REWRITER}.get(mode, EDITOR)
    passes = 2 if mode == "fix" else 1
    texts, handle = _read(path)
    if not any(t.strip() for t in texts):
        scan = " If it's a scan, it may be too faint to read." if path.suffix.lower() == ".pdf" else ""
        raise ValueError(f"I couldn't find any text in {path.name}.{scan}")
    edits: dict[int, str] = {}
    changes: list[str] = []
    cost = 0.0
    notes: list[str] = []
    held: list[str] = []  # edits that were not made, and why: these notes come first
    queue = [(chunk, False) for chunk in _chunks(texts)]
    while queue:
        chunk, second_try = queue.pop(0)
        # Two passes: edit with a checklist, then review the result for anything still wrong.
        current = {i: texts[i] for i in chunk}
        said: list[str] = []
        answered: set[int] = set()
        unread = False
        for attempt in range(passes):
            numbered = "\n".join(f"[{i}] {current[i]}" for i in chunk)
            ask = (
                f"Instructions from the user: {instructions or 'Fix the grammar, spelling and punctuation.'}\n\n{numbered}"
                if attempt == 0
                else "Review this edited text as a second editor. Fix anything still wrong; if it is all correct, "
                f"return empty edits.\n\n{numbered}"
            )
            response = await llm.complete(
                system=system, messages=[Message.user(ask)], max_tokens=8000
            )
            cost += response.usage.cost_usd
            data = _parse(response.text)
            if data is None:
                unread = attempt == 0  # a review that can't be read leaves the first pass standing
                break
            found = data.get("edits")
            for e in found if isinstance(found, list) else []:
                try:
                    i = int(e["i"])
                except (KeyError, TypeError, ValueError):
                    continue
                new = str(e.get("text", "")).strip()
                if i in current and new:
                    answered.add(i)
                    current[i] = new
            listed_changes = data.get("changes")
            for c in listed_changes if isinstance(listed_changes, list) else []:
                if (
                    mode == "fix"
                    and isinstance(c, dict)
                    and _numbers(str(c.get("from", ""))) != _numbers(str(c.get("to", "")))
                ):
                    held.append(
                        f"Not made, because it would change a number: '{_clip(str(c.get('from', '')))}'"
                        f" to '{_clip(str(c.get('to', '')))}'."
                    )
                elif _describe(c):
                    said.append(_describe(c))
            more = data.get("notes")
            notes += [str(n) for n in (more if isinstance(more, list) else []) if str(n).strip()]
        if unread:
            if len(chunk) > 1 and not second_try:
                half = len(chunk) // 2  # a shorter answer may fit where the long one was cut off
                queue[:0] = [(chunk[:half], True), (chunk[half:], True)]
                continue
            raise RuntimeError(
                f"The answer for {path.name} came back cut off, so nothing was saved. Ask again."
            )
        fresh: list[int] = []
        left = 0  # paragraphs whose edit was not made
        for i in chunk:
            if current[i].strip() == texts[i].strip():
                continue
            if mode == "fix" and _numbers(current[i]) != _numbers(texts[i]):
                held.append(
                    f"Left as it was, because the edit changed a number: '{_clip(texts[i].strip())}'"
                )
                left += 1
                continue
            if handle is not None and not _patch(handle[1][i], current[i]):
                held.append(
                    "Left as it was, because the change runs across a link or a field that can't "
                    f"be edited safely: '{_clip(texts[i].strip())}'"
                )
                left += 1
                continue
            edits[i] = current[i]
            fresh.append(i)
        if mode == "translate":
            missing = [i for i in chunk if i not in answered and _has_words(texts[i])]
            if missing and not second_try:
                queue.insert(0, (missing, True))  # asked once more, for just those
            elif missing:
                raise RuntimeError(
                    f"The translation of {path.name} came back with {len(missing)} "
                    f"paragraph{'s' if len(missing) != 1 else ''} missing, so nothing was saved. Ask again."
                )
        if mode == "fix" and (not said or (left and path.suffix.lower() != ".pdf")):
            # The model changed paragraphs without saying what, or its list names an edit that
            # was not made: work the list out from the words. (A PDF's corrections are this
            # list; empty, the file said "Nothing to correct.")
            said = [c for i in fresh for c in _diff_changes(texts[i].strip(), edits[i].strip())]
        changes += said

    notes = list(dict.fromkeys(held + notes))
    label = {"translate": "translated by Pyxi", "rewrite": "rewritten by Pyxi"}.get(mode, "fixed by Pyxi")
    listed = mode == "fix" and path.suffix.lower() == ".pdf"
    if listed:
        # A PDF is a picture of a page: its words can't be changed where they are, and typing
        # them out again loses the columns, the spacing and the fonts (live: a resume came
        # back as one block of text in a Word file, twice). So the corrections are listed.
        out = _list_corrections(path, changes, notes)
    else:
        out = _write(path, texts, edits, handle, label)
    return FixResult(
        path=out,
        changed=len(changes) if listed else len(edits),
        total=sum(1 for t in texts if t.strip()),
        changes=changes[:40] if listed else changes[:12],
        notes=notes[:5],
        cost_usd=cost,
        listed=listed,
    )


def _list_corrections(path: Path, changes: list[str], notes: list[str]) -> Path:
    from pyxi.agents.pixel_common import out_dir_for, unique_path

    lines = [f"Corrections for {path.name}", ""]
    lines += [f"{n}. {c}" for n, c in enumerate(changes, 1)] or ["Nothing to correct."]
    if notes:
        lines += ["", "Worth a look:"] + [f"- {n}" for n in notes]
    lines += [
        "",
        "A PDF's layout can't be edited, so nothing in it was changed. Make these in the file "
        "the PDF was made from (Word, Google Docs, Canva), and export it again.",
    ]
    out = unique_path(out_dir_for(path), f"{path.stem} (corrections by Pyxi).txt")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _write(
    path: Path, texts: list[str], edits: dict[int, str], handle, label: str = "fixed by Pyxi"
) -> Path:
    from pyxi.agents.pixel_common import out_dir_for, unique_path

    stem = path.stem
    ext = path.suffix.lower()
    folder = out_dir_for(path)
    if ext == ".docx" and handle is not None:
        doc, _ = handle  # the edits are already in its paragraphs (see _patch)
        out = unique_path(folder, f"{stem} ({label}).docx")
        doc.save(str(out))
        return out
    final = [edits.get(i, t) for i, t in enumerate(texts)]
    if ext == ".pdf":
        from docx import Document

        doc = Document()
        for t in final:
            doc.add_paragraph(t)
        out = unique_path(folder, f"{stem} ({label}).docx")
        doc.save(str(out))
        return out
    out = unique_path(folder, f"{stem} ({label}){path.suffix}")
    out.write_text("\n".join(final), encoding="utf-8")
    return out


SUMMARY_EXTS = {".docx", ".txt", ".md", ".pdf", ".xlsx", ".pptx", ".html", ".htm", ".csv"}
SUMMARY_CHARS = 60000  # how much of a document one summary reads


@dataclass
class SummaryResult:
    path: Path
    text: str
    cost_usd: float = 0.0


def _whole_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pyxi.agents.pixel_pdf import page_texts

        texts, _ = page_texts(path)
        return "\n\n".join(f"[page {i}]\n{t}" for i, t in enumerate(texts, 1))
    if ext in (".docx", ".pptx", ".xlsx", ".html", ".htm"):
        from pyxi.agents.pixel_pdf import document_lines

        return "\n".join(document_lines(path))
    from pyxi.agents.pixel_render import read_any_text

    return read_any_text(path)


def document_text(path: Path, limit: int = SUMMARY_CHARS) -> str:
    """The words of a document (PDF scans read with OCR), for summaries."""
    return _whole_text(path)[:limit]


async def summarize_document(path: Path, instructions: str, llm: LLMProvider) -> SummaryResult:
    """A summary saved beside the document as "<name> (summary).md"; the text comes back too."""
    from pyxi.agents.pixel_common import out_dir_for, unique_path

    if path.suffix.lower() not in SUMMARY_EXTS:
        raise ValueError(f"I can summarize {', '.join(sorted(SUMMARY_EXTS))} files, not {path.suffix}.")
    whole = _whole_text(path)
    text = whole[:SUMMARY_CHARS]
    if not text.strip():
        raise ValueError(f"I couldn't find any text in {path.name}.")
    response = await llm.complete(
        system=SUMMARIZER,
        messages=[Message.user(f"Instructions: {instructions}\n\nDocument {path.name}:\n{text}")],
        max_tokens=2000,
    )
    summary = response.text.strip()
    if not summary:
        raise RuntimeError("The summary came back empty.")
    if len(whole) > len(text):
        # Said, not left for them to find out: the amount due on the last page wasn't read.
        share = round(100 * len(text) / len(whole))
        summary += (
            f"\n\nNote: {path.name} is long, and this covers only the first {share}% of it "
            f"({len(text):,} of {len(whole):,} characters). The rest was not read."
        )
    out = unique_path(out_dir_for(path), f"{path.stem} (summary).md")
    out.write_text(summary, encoding="utf-8")
    return SummaryResult(out, summary, response.usage.cost_usd)
