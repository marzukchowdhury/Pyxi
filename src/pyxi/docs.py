"""Fix documents: grammar, spelling, clarity or whatever the instructions say, keeping the file a
file. Word documents keep their paragraph styles (headings stay headings) and are saved as a
copy beside the original, "<name> (fixed by Pyxi).docx", so nothing is ever overwritten. Plain
text and Markdown stay text; PDFs come back as an editable Word copy.
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


async def fix_document(path: Path, instructions: str, llm: LLMProvider) -> FixResult:
    if path.suffix.lower() not in SUPPORTED:
        raise ValueError(f"I can fix {', '.join(sorted(SUPPORTED))} files, not {path.suffix}.")
    texts, handle = _read(path)
    edits: dict[int, str] = {}
    changes: list[str] = []
    cost = 0.0
    notes: list[str] = []
    for chunk in _chunks(texts):
        # Two passes: edit with a checklist, then review the result for anything still wrong.
        current = {i: texts[i] for i in chunk}
        for attempt in range(2):
            numbered = "\n".join(f"[{i}] {current[i]}" for i in chunk)
            ask = (
                f"Instructions from the user: {instructions or 'Fix the grammar, spelling and punctuation.'}\n\n{numbered}"
                if attempt == 0
                else "Review this edited text as a second editor. Fix anything still wrong; if it is all correct, "
                f"return empty edits.\n\n{numbered}"
            )
            response = await llm.complete(
                system=EDITOR, messages=[Message.user(ask)], max_tokens=8000
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

    out = _write(path, texts, edits, handle)
    return FixResult(
        path=out,
        changed=len(edits),
        total=sum(1 for t in texts if t.strip()),
        changes=changes[:12],
        notes=list(dict.fromkeys(notes))[:5],
        cost_usd=cost,
    )


def _write(path: Path, texts: list[str], edits: dict[int, str], handle) -> Path:
    stem = path.stem
    ext = path.suffix.lower()
    if ext == ".docx" and handle is not None:
        doc, paras = handle
        for i, new in edits.items():
            _set_text(paras[i], new)
        out = path.with_name(f"{stem} (fixed by Pyxi).docx")
        doc.save(str(out))
        return out
    final = [edits.get(i, t) for i, t in enumerate(texts)]
    if ext == ".pdf":
        from docx import Document

        doc = Document()
        for t in final:
            doc.add_paragraph(t)
        out = path.with_name(f"{stem} (fixed by Pyxi).docx")
        doc.save(str(out))
        return out
    out = path.with_name(f"{stem} (fixed by Pyxi){path.suffix}")
    out.write_text("\n".join(final), encoding="utf-8")
    return out
