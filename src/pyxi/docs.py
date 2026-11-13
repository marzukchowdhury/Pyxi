"""Quill's document work: fix (grammar, spelling, clarity), translate, rewrite or summarize,
keeping the file a file. Word documents keep their paragraph styles (headings stay headings) and are saved as a
copy beside the original, "<name> (fixed by Pyxi).docx", so nothing is ever overwritten. Plain
text and Markdown stay text. A PDF's own text can't be edited, so a PDF comes back as a Word
copy beside it: for a fix (grammar, spelling) Pixel's converter turns it into Word first, with
its headings, lists and pictures, and the corrections are made there. Only a scan, which has
no text in it at all, comes back as the list of corrections to make.

A fix in a Word file changes only the words that changed: each edit is put into the run it
belongs to, so bold stays bold, a link stays a link, and a picture, a tab or a page break in
the same paragraph is never touched.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
from collections import Counter
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
_SUMMARY = (
    r"(summar(?:y|ies)|gist|overview|digest|rundown|recap|highlights|takeaways|key points|main points)"
)
_IT = r"(?:it|this|that|them|these|everything)"

# What a Quill document job does, from the words of the task. These ask for it outright:
# "summarize it", "give me the gist", "what does this say", "a summary of the errors" (the
# noun where a sentence starts, but not the name of a part, "summary section", or a thing to
# keep, "the gist should stay"); "translate into French", "French version", "make this
# German"; "make it shorter", "cut this down", "correct the tone".
MODES = {
    "summarize": r"\b(summari[sz]|sum (it |this |that |them )?up|tl;?dr|brief me|"
    r"(give|get|write|send|make|create|prepare|need|want|like)\s+(me\s+|us\s+)?"
    rf"(?:(?:an?|the|some)\s+(?:\w+\s+){{0,2}})?{_SUMMARY}\b|"
    rf"what(?:'s| is| are)\s+(?:the\s+)?(?:\w+\s+)?{_SUMMARY}\b|"
    r"what\s+do(?:es)?\s+(?:it|this|that|they|the\s+\w+)\s+say\b|"
    r"what(?:'s| is)\s+(?:it|this|that|the)\b[^.?!]{0,30}?\babout\b|"
    r"what(?:'s| is)\s+in\s+(?:it|this|that|the)\b|"
    r"explain\s+(?:it|this|that|the)\b)|"
    r"(?:^\W*|[.;:!?]\s*|\b(?:then|also|plus)\s+)(?:(?:an?|the|short|quick|brief|one[- ]page)\s+)*"
    rf"{_SUMMARY}\b(?!\s+(?:section|part|paragraph|page|heading|title|should|must|is|was|stays?|remains?)\b)",
    "translate": rf"\b(translat|into {_LANGUAGE}\b|"
    rf"(put|turn|render|convert|write|make|get|have|need|want|like)\s+{_IT}\s+(?:to be\s+)?"
    rf"(?:in|into|to)\s+{_LANGUAGE}\b|"
    rf"make\s+{_IT}\s+{_LANGUAGE}\b|convert\s+(?:{_IT}\s+)?(?:in)?to\s+{_LANGUAGE}\b|"
    rf"{_LANGUAGE}\s+version\b|{_LANGUAGE}\s+to\s+{_LANGUAGE}\b)",
    "rewrite": r"\b(rewrite|re-write|rephrase|reword|paraphras|simplif|shorten|condens|tighten|"
    rf"cut\s+(?:{_IT}\s+)?down|"
    r"make\s+(?:it|this|that|them|(?:my|the)\s+\w+)\s+(?:\w+\s+)?"
    r"(?:shorter|longer|simpler|clearer|tighter|friendlier|more|less|sound)\b|"
    r"more (formal|professional|friendly|casual|concise)|friendlier|plain english|"
    r"(?:fix|correct|improve|change|adjust|soften)\s+(?:the|its|my)\s+tone\b)",
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


def _quoted(m: re.Match[str]) -> str:
    """What stands in for a quoted string: nothing for a file's name ('Key Points.docx') or a
    title, the words themselves when they are the instruction (The user said: "make it more
    formal"), which taking every quote out had deleted."""
    inner = m.group(0)[1:-1].strip()
    low = inner.lower()
    if _FILE_NAME.fullmatch(inner) or not (
        _PROOFREAD.search(low) or any(re.search(p, low) for p in (*MODES.values(), *HINTS.values()))
    ):
        return " "
    return f" {inner} "


def _without_names(task: str) -> str:
    """The task without the files it names: full paths, a name or title in quotes, and a name
    ending in a document extension together with the capitalised words that lead up to it."""
    task = _QUOTED.sub(_quoted, _FILE_PATH.sub(" ", task))
    words = task.split()
    # "Give Me A Summary Of Report.pdf": when every word has a capital, capitals say nothing
    # about where the file's name starts, so only the last word of it goes.
    titled = all(w[:1].isupper() or not w[:1].isalpha() for w in words)
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
            and not titled
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
    listed: bool = False  # a scanned PDF: its corrections are listed, nothing was typed out
    converted: bool = False  # a typed PDF: fixed in a Word copy of it


PARALLEL = 4  # parts of one document with the model at the same time


@dataclass
class _Part:
    """What came of one part of a document, kept apart until every part is back."""

    changes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    lost: list[int] = field(default_factory=list)


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


# Scripts written without spaces between words: Thai, Lao, Burmese, Khmer, Japanese, Chinese.
_NO_SPACES = re.compile(
    "["
    + "".join(
        f"{chr(a)}-{chr(b)}"
        for a, b in ((0x0E00, 0x0EFF), (0x1000, 0x109F), (0x1780, 0x17FF), (0x3040, 0x30FF), (0x3400, 0x9FFF))
    )
    + "]"
)


def _spaceless(text: str) -> bool:
    """Is this mostly in a script with no spaces to line words up by?"""
    letters = [c for c in text if c.isalnum()]
    return sum(bool(_NO_SPACES.match(c)) for c in letters) * 3 > len(letters)


def _replace_whole(p: Any, new: str) -> bool | None:
    """Make a paragraph read ``new`` when every word of it has changed (a translation, a
    rewrite) and the changes can't be placed one by one. The words go into the paragraph's
    own runs, each run keeping its share of the line by position: a bold label at the start
    is still a bold start, and a link is still a link, though it may sit on different words
    than before (True says so). Pictures, tabs, breaks and fields all stay. None: there was
    nowhere to put text, and nothing was changed.

    Live in review: a paragraph with a link, translated into Chinese (no spaces to line words
    up by), was refused by the patch and left in English in a "translated" file."""
    atoms = _atoms(p)
    plain = [x for x in atoms if x.plain]
    if not plain:
        return None
    old = "".join(x.text for x in atoms)
    new = old[: len(old) - len(old.lstrip())] + new.strip() + old[len(old.rstrip()) :]
    total = sum(len(x.text) for x in plain) or 1
    # Where a run may end: between words, or anywhere in a script written without spaces.
    # After punctuation or a space is best ("Deadline:" in bold stays a whole label).
    cuts, clean = [0], {0}
    for k in range(1, len(new)):
        inside_word = new[k - 1].isalnum() and new[k].isalnum()
        if not new[k - 1].isalnum() and new[k].isalnum():
            clean.add(k)
        if not inside_word or _NO_SPACES.match(new[k - 1]) or _NO_SPACES.match(new[k]):
            cuts.append(k)
    cuts.append(len(new))
    at, done = 0, 0
    space = "{http://www.w3.org/XML/1998/namespace}space"
    for n, x in enumerate(plain):
        done += len(x.text)
        want = len(new) * done / total
        if n == len(plain) - 1:
            end = len(new)
        elif not x.text:
            end = at
        else:
            ahead = [c for c in cuts if c >= at]
            near = [c for c in ahead if c in clean and abs(c - want) <= 3]
            end = min(near or ahead, key=lambda c: abs(c - want))
        x.el.text = new[at:end]
        x.el.set(space, "preserve")
        at = end
    zones = {id(x.zone) for x in plain}
    return len(zones) > 1


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
    """The model's JSON. An answer in words, with no JSON in it ("Everything here is already
    correct."), is no edits. None is JSON that can't be read: cut off, most often."""
    text = text or ""
    if "{" not in text:
        return {}
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


_UNITS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen",
]  # fmt: skip
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORDINALS = [
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
    "tenth", "eleventh", "twelfth",
]  # fmt: skip
_NUMBER_WORDS: dict[str, float] = {
    **{w: float(n) for n, w in enumerate(_UNITS)},
    **{w: float(20 + 10 * n) for n, w in enumerate(_TENS)},
    **{w: float(n) for n, w in enumerate(_ORDINALS, 1)},
    "hundred": 100.0,
    "thousand": 1000.0,
}


def _digit_numbers(text: str) -> Counter[float]:
    """The numbers written in digits, by value: "$1,200.00", "1,200" and "1200" are one number,
    "05" is 5, and "3/5/2024" is 3, 5 and 2024."""
    found = re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", text)
    return Counter(float(n.replace(",", "")) for n in found)


def _word_numbers(text: str) -> Counter[float]:
    """The numbers written as words: "three", "second", "twenty-one"."""
    low = text.lower()
    tens = "|".join(_TENS)
    out: Counter[float] = Counter()
    for ten, unit in re.findall(rf"\b({tens})[- ]({'|'.join(_UNITS[1:10])})\b", low):
        out[_NUMBER_WORDS[ten] + _NUMBER_WORDS[unit]] += 1
    low = re.sub(rf"\b({tens})[- ]({'|'.join(_UNITS[1:10])})\b", " ", low)
    out.update(_NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", low) if w in _NUMBER_WORDS)
    return out


def _same_numbers(old: str, new: str) -> bool:
    """Do two wordings say the same numbers? Compared by value, and a number spelt out counts
    as that number ("2nd" and "second", "3 years" and "three years"). Words alone never fail
    it: "one" and "second" are not always numbers."""
    was, now = _digit_numbers(old), _digit_numbers(new)
    if was == now:
        return True
    gone, came = was - now, now - was
    words_was, words_now = _word_numbers(old), _word_numbers(new)
    return not (gone - (words_now - words_was)) and not (came - (words_was - words_now))


def _keep_numbers(old: str, new: str) -> tuple[str, list[str]]:
    """``new`` without the edits that change a number, and a note for each one left out. The
    rest of the paragraph's fixes are still made: "I worked their for 3 years, since 2019" to
    "there for 4 years" keeps "their" to "there" and leaves the 3. (Dropping the paragraph's
    every fix for one number left their/there wrong, and "2nd" to "second" undone.)"""
    if _same_numbers(old, new):
        return new, []
    a, b = list(re.finditer(r"\S+", old)), list(re.finditer(r"\S+", new))
    wa, wb = [m.group() for m in a], [m.group() for m in b]

    def pieces(text: str, words: list[re.Match[str]], lo: int, hi: int) -> list[tuple[str, str]]:
        """(word, the white space after it) for words lo to hi."""
        ends = [m.start() for m in words[1:]] + [len(text)]
        return [(words[k].group(), text[words[k].end() : ends[k]]) for k in range(lo, hi)]

    out: list[tuple[str, str]] = []
    notes: list[str] = []
    matcher = difflib.SequenceMatcher(a=wa, b=wb, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            out += pieces(new, b, j1, j2)
            continue
        # Word for word where the counts match, so one changed number doesn't take the fix
        # next to it ("owe $1,200" to "owes $1,300": "owes" is made, the amount is not).
        if op == "replace" and i2 - i1 == j2 - j1:
            spans = [((i1 + k, i1 + k + 1), (j1 + k, j1 + k + 1)) for k in range(i2 - i1)]
        else:
            spans = [((i1, i2), (j1, j2))]
        for (x1, x2), (y1, y2) in spans:
            was, now = " ".join(wa[x1:x2]), " ".join(wb[y1:y2])
            if _same_numbers(was, now):
                out += pieces(new, b, y1, y2)
                continue
            out += pieces(old, a, x1, x2)
            if was and now:
                notes.append(f"Left '{_clip(was)}' as it was: '{_clip(now)}' would change a number.")
            elif was:
                notes.append(f"Left '{_clip(was)}' in: taking it out would drop a number.")
            else:
                notes.append(f"Did not add '{_clip(now)}': it has a number that wasn't there.")
    text = "".join(w + (gap or " ") for w, gap in out)
    return text.strip(), notes


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
    read is asked for again in two halves; if that part still can't be read it is left as it
    was, with a note, and the rest is saved (nothing readable at all is a failure, never a
    copy with nothing done to it). A translation that skips paragraphs is a failure. And in a
    grammar fix, an edit that changes a number is left out, with a note; the paragraph's other
    fixes are still made."""
    from pyxi.agents.pixel_common import word_text

    if path.suffix.lower() not in SUPPORTED:
        raise ValueError(f"I can work on {', '.join(sorted(SUPPORTED))} files, not {path.suffix}.")
    system = {"translate": TRANSLATOR, "rewrite": REWRITER}.get(mode, EDITOR)
    passes = 2 if mode == "fix" else 1
    # A PDF to fix is fixed in a Word copy of it, made by Pixel's own converter (a scan has no
    # text to convert: its corrections are listed instead).
    as_word = None
    if mode == "fix" and path.suffix.lower() == ".pdf":
        as_word = await asyncio.to_thread(_pdf_as_word, path)
    texts, handle = as_word or _read(path)
    if not any(t.strip() for t in texts):
        scan = " If it's a scan, it may be too faint to read." if path.suffix.lower() == ".pdf" else ""
        raise ValueError(f"I couldn't find any text in {path.name}.{scan}")
    edits: dict[int, str] = {}
    changes: list[str] = []
    cost = 0.0
    notes: list[str] = []
    held: list[str] = []  # edits that were not made, and why: these notes come first
    order = [i for chunk in _chunks(texts) for i in chunk]
    lost: list[int] = []  # paragraphs whose answer couldn't be read, twice
    read_some = False
    # The parts of a long document are sent to the model side by side, a few at a time: one
    # after another, a ten-page proposal took 102 seconds, nearly all of it waiting in line.
    gate = asyncio.Semaphore(PARALLEL)

    async def work(chunk: list[int], second_try: bool) -> list[_Part]:
        nonlocal cost, read_some
        part = _Part()
        # Two passes: edit with a checklist, then review the result for anything still wrong.
        current = {i: texts[i] for i in chunk}
        said: list[str] = []
        unlisted: list[str] = []  # changes the model listed that would change a number
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
            async with gate:
                response = await llm.complete(
                    system=system, messages=[Message.user(ask)], max_tokens=8000
                )
            cost += response.usage.cost_usd
            data = _parse(response.text)
            if data is None:
                unread = attempt == 0  # a review that can't be read leaves the first pass standing
                break
            read_some = True
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
                    and not _same_numbers(str(c.get("from", "")), str(c.get("to", "")))
                ):
                    unlisted.append(
                        f"Left '{_clip(str(c.get('from', '')))}' as it was: "
                        f"'{_clip(str(c.get('to', '')))}' would change a number."
                    )
                elif _describe(c):
                    said.append(_describe(c))
            more = data.get("notes")
            part.notes += [str(n) for n in (more if isinstance(more, list) else []) if str(n).strip()]
        if unread:
            if len(chunk) > 1 and not second_try:
                half = len(chunk) // 2  # a shorter answer may fit where the long one was cut off
                first, second = await _together(work(chunk[:half], True), work(chunk[half:], True))
                return [*first, *second]
            if mode == "translate":  # a translation with a part left out is not a translation
                raise RuntimeError(
                    f"The answer for {path.name} came back cut off, so nothing was saved. Ask again."
                )
            part.lost += chunk  # this part stays as it was, said in a note; the rest is still done
            return [part]
        held = part.held
        fresh: list[int] = []
        left = 0  # paragraphs with an edit that was not made
        for i in chunk:
            new = current[i].strip()
            if new == texts[i].strip():
                continue
            if mode == "fix":
                new, kept = _keep_numbers(texts[i].strip(), new)
                held += kept
                left += bool(kept)
                if new == texts[i].strip():
                    continue
            if handle is not None:
                el = handle[1][i]
                # A translation into or out of a script without spaces has no words to line
                # up: placed word by word, the whole line landed in the first run (all bold).
                by_word = mode == "fix" or not (_spaceless(new) or _spaceless(texts[i]))
                if not (by_word and _patch(el, new)):
                    quoted = _clip(texts[i].strip())
                    if mode == "fix":
                        held.append(
                            "Left as it was, because the change runs across a link or a field "
                            f"that can't be edited safely: '{quoted}'"
                        )
                        left += 1
                        continue
                    # Every word has changed anyway: the text goes in whole.
                    moved = _replace_whole(el, new)
                    if moved is None:
                        held.append(f"Left as it was, because it has no text to change: '{quoted}'")
                        continue
                    if moved:
                        held.append(
                            f"A link in this line may sit on different words now, check it: '{_clip(new)}'"
                        )
                new = word_text(el)  # what the paragraph reads now
                if new == texts[i]:
                    continue  # only retyped (a tab as spaces): nothing changed, so not counted
            else:  # a line of text keeps its indent
                was = texts[i]
                new = was[: len(was) - len(was.lstrip())] + new + was[len(was.rstrip()) :]
            edits[i] = new
            fresh.append(i)
        if not left:
            held += unlisted
        again: list[int] = []
        if mode == "translate":
            missing = [i for i in chunk if i not in answered and _has_words(texts[i])]
            if missing and not second_try:
                again = missing  # asked once more, for just those
            elif missing:
                raise RuntimeError(
                    f"The translation of {path.name} came back with {len(missing)} "
                    f"paragraph{'s' if len(missing) != 1 else ''} missing, so nothing was saved. Ask again."
                )
        if mode == "fix" and (not said or left):
            # The model changed paragraphs without saying what, or its list names an edit that
            # was not made: work the list out from the words. (A PDF's corrections are this
            # list; empty, the file said "Nothing to correct.")
            said = [c for i in fresh for c in _diff_changes(texts[i].strip(), edits[i].strip())]
        part.changes = said
        return [part, *(await work(again, True) if again else [])]

    for parts in await _together(*(work(chunk, False) for chunk in _chunks(texts))):
        for part in parts:  # put together in the document's own order, whichever came back first
            changes += part.changes
            held += part.held
            notes += part.notes
            lost += part.lost

    if lost and not read_some:
        raise RuntimeError(
            f"The answer for {path.name} came back cut off, so nothing was saved. Ask again."
        )
    if lost:
        # Said by where it starts and ends: "paragraphs 21 to 30" means little in Word.
        place = {i: k for k, i in enumerate(order)}
        lost.sort(key=place.__getitem__)
        runs: list[list[int]] = []
        for i in lost:
            if runs and place[i] == place[runs[-1][-1]] + 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        for run in runs:
            n = len(run)
            span = f"'{_clip(texts[run[0]].strip(), 40)}'" + (
                f" to '{_clip(texts[run[-1]].strip(), 40)}'" if n > 1 else ""
            )
            held.append(
                f"{n} paragraph{'s were' if n != 1 else ' was'} left as {'they were' if n != 1 else 'it was'}"
                f", because the answer for {'them' if n != 1 else 'it'} couldn't be read: {span}. "
                "Ask again for that part."
            )
    notes = list(dict.fromkeys(held + notes))
    label = {"translate": "translated by Pyxi", "rewrite": "rewritten by Pyxi"}.get(mode, "fixed by Pyxi")
    listed = mode == "fix" and path.suffix.lower() == ".pdf" and as_word is None
    if listed:
        # A scan is a picture of each page: there are no words in it to change, and typing
        # out what a text reader made of it is not their document. So the corrections are
        # listed. (A typed PDF used to get the list too: "why is it a txt file that she
        # gave me?" It is fixed in a Word copy now, which is a file they can send.)
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
        converted=as_word is not None,
    )


def _pdf_as_word(path: Path) -> tuple[list[str], object] | None:
    """A typed PDF as a Word document, by Pixel's own converter (headings, paragraphs, lists,
    bold and pictures come through): its paragraphs and the document itself, as :func:`_read`
    gives them for a .docx. None for a scan, a PDF with no typed text on any page. A locked or
    broken PDF says so in a sentence."""
    import shutil
    import tempfile

    from pyxi.agents.pixel_pdf import page_texts, pdf_to_word

    typed, _ = page_texts(path, ocr=False)
    if not any(len(re.sub(r"\s", "", t)) >= 25 for t in typed):
        return None
    tmp = Path(tempfile.mkdtemp(prefix="pyxi_quill_"))
    try:  # read into memory: the copy that is kept is saved once, with the fixes in it
        return _read(pdf_to_word([path], tmp).outputs[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _together(*jobs: Any) -> list[Any]:
    """These at once, their results in the order given. If one fails the rest are stopped
    (left running, they would go on spending on a document that is not going to be saved)."""
    tasks = [asyncio.ensure_future(j) for j in jobs]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for t in tasks:
            t.cancel()
        raise


def _list_corrections(path: Path, changes: list[str], notes: list[str]) -> Path:
    from pyxi.agents.pixel_common import out_dir_for, unique_path

    lines = [f"Corrections for {path.name}", ""]
    lines += [f"{n}. {c}" for n, c in enumerate(changes, 1)] or ["Nothing to correct."]
    if notes:
        lines += ["", "Worth a look:"] + [f"- {n}" for n in notes]
    lines += [
        "",
        "This PDF is a scan: a picture of each page, with no text in it that can be changed. "
        "The words above were read off the picture, so check them against the page. Make the "
        "corrections in the file it was printed or scanned from.",
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
    if handle is not None:  # a Word file, or the Word copy of a PDF
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
