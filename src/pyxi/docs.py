"""Quill's document work: fix (grammar, spelling, clarity), translate, rewrite or summarize,
keeping the file a file. Word documents keep their paragraph styles (headings stay headings) and are saved as a
copy beside the original, "<name> (fixed by Pyxi).docx", so nothing is ever overwritten. Plain
text and Markdown stay text. A PDF's layout can't be edited: a translation or a rewrite of one
comes back as a Word copy, and a fix (grammar, spelling) comes back as the list of corrections
to make in the original, never as the page typed out again.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

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

# What a Quill document job does, from the words of the task.
MODES = {
    "summarize": r"\b(summar|sum up|tl;?dr|gist|key points|main points|overview|brief me|digest)",
    "translate": r"\b(translat|in(to)? (french|spanish|german|italian|portuguese|arabic|bengali|bangla|hindi|urdu|"
    r"chinese|mandarin|japanese|korean|russian|turkish|dutch|polish|vietnamese|tagalog|punjabi|tamil|farsi|persian))",
    "rewrite": r"\b(rewrite|re-write|rephrase|reword|simplif|shorten|make it (more|less|sound)|more (formal|"
    r"professional|friendly|casual|concise)|friendlier|plain english|tone)",
}


# "Don't rewrite it", "without rephrasing", "keep the tone": the opposite of asking for one.
_NOT_THAT = re.compile(
    r"\b(don'?t|do not|never|no|not|nor|or|without|avoid|keep(ing)?|same|preserv\w*|leave)\b(?:\W+\w+){0,4}?\W+$"
)
_PROOFREAD = re.compile(r"\b(grammar|spelling|punctuation|typos?|proof-?read|mistakes?|errors?)\b")


def document_mode(task: str) -> str:
    """fix, translate, rewrite or summarize. A word that is only there to rule it out ("fix
    the grammar, don't rewrite anything") doesn't count. Live: a resume sent for a grammar
    check, "fix them only, keep the layout", was rewritten from top to bottom because the
    task named the thing it was told not to do."""
    low = task.lower()
    for mode in ("summarize", "translate", "rewrite"):
        asked = [m for m in re.finditer(MODES[mode], low) if not _NOT_THAT.search(low[: m.start()])]
        if asked and not (mode == "rewrite" and _PROOFREAD.search(low) and "only" in low):
            return mode
    return "fix"


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

        doc = Document(str(path))
        paras = [p for p in _docx_paragraphs(doc)]
        return [p.text for p in paras], (doc, paras)
    if ext == ".pdf":
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        blocks = [re.sub(r"\s*\n\s*", " ", b).strip() for b in re.split(r"\n\s*\n", text)]
        return [b for b in blocks if b], None
    return path.read_text(encoding="utf-8", errors="replace").split("\n"), None


def _docx_paragraphs(doc):
    yield from doc.paragraphs
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def _set_text(paragraph, text: str) -> None:
    """Replace a paragraph's text but keep its style and its first run's formatting."""
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


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


async def fix_document(
    path: Path, instructions: str, llm: LLMProvider, *, mode: str = "fix"
) -> FixResult:
    """Fix (two passes: edit, then review), translate or rewrite (one pass) each paragraph, and
    save a copy beside the original: "<name> (fixed by Pyxi)", "(translated by Pyxi)"..."""
    if path.suffix.lower() not in SUPPORTED:
        raise ValueError(f"I can work on {', '.join(sorted(SUPPORTED))} files, not {path.suffix}.")
    system = {"translate": TRANSLATOR, "rewrite": REWRITER}.get(mode, EDITOR)
    passes = 2 if mode == "fix" else 1
    texts, handle = _read(path)
    edits: dict[int, str] = {}
    changes: list[str] = []
    cost = 0.0
    notes: list[str] = []
    for chunk in _chunks(texts):
        # Two passes: edit with a checklist, then review the result for anything still wrong.
        current = {i: texts[i] for i in chunk}
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
            for e in data.get("edits", []):
                try:
                    i = int(e["i"])
                except (KeyError, TypeError, ValueError):
                    continue
                new = str(e.get("text", "")).strip()
                if i in chunk and new and new != current[i].strip():
                    current[i] = new
            changes += [_describe(c) for c in data.get("changes", []) if _describe(c)]
            notes += [str(n) for n in data.get("notes", []) if str(n).strip()]
        for i in chunk:
            if current[i].strip() != texts[i].strip():
                edits[i] = current[i]

    label = {"translate": "translated by Pyxi", "rewrite": "rewritten by Pyxi"}.get(mode, "fixed by Pyxi")
    listed = mode == "fix" and path.suffix.lower() == ".pdf"
    if listed:
        # A PDF is a picture of a page: its words can't be changed where they are, and typing
        # them out again loses the columns, the spacing and the fonts (live: a resume came
        # back as one block of text in a Word file, twice). So the corrections are listed.
        out = _list_corrections(path, changes, list(dict.fromkeys(notes)))
    else:
        out = _write(path, texts, edits, handle, label)
    return FixResult(
        path=out,
        changed=len(changes) if listed else len(edits),
        total=sum(1 for t in texts if t.strip()),
        changes=changes[:40] if listed else changes[:12],
        notes=list(dict.fromkeys(notes))[:5],
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
        doc, paras = handle
        for i, new in edits.items():
            _set_text(paras[i], new)
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


@dataclass
class SummaryResult:
    path: Path
    text: str
    cost_usd: float = 0.0


def document_text(path: Path, limit: int = 60000) -> str:
    """The words of a document (PDF scans read with OCR), for summaries."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pyxi.agents.pixel_pdf import page_texts

        texts, _ = page_texts(path)
        text = "\n\n".join(f"[page {i}]\n{t}" for i, t in enumerate(texts, 1))
    elif ext in (".docx", ".pptx", ".xlsx", ".html", ".htm"):
        from pyxi.agents.pixel_pdf import document_lines

        text = "\n".join(document_lines(path))
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit]


async def summarize_document(path: Path, instructions: str, llm: LLMProvider) -> SummaryResult:
    """A summary saved beside the document as "<name> (summary).md"; the text comes back too."""
    from pyxi.agents.pixel_common import out_dir_for, unique_path

    if path.suffix.lower() not in SUMMARY_EXTS:
        raise ValueError(f"I can summarize {', '.join(sorted(SUMMARY_EXTS))} files, not {path.suffix}.")
    text = document_text(path)
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
    out = unique_path(out_dir_for(path), f"{path.stem} (summary).md")
    out.write_text(summary, encoding="utf-8")
    return SummaryResult(out, summary, response.usage.cost_usd)
