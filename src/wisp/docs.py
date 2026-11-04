"""Fix documents: grammar, spelling, clarity or whatever the instructions say, keeping the file a
file. Word documents keep their paragraph styles (headings stay headings) and are saved as a
copy beside the original, "<name> (fixed by Wisp).docx", so nothing is ever overwritten. Plain
text and Markdown stay text; PDFs come back as an editable Word copy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from otto.llm.base import LLMProvider, Message

SUPPORTED = {".docx", ".txt", ".md", ".pdf"}
CHUNK_CHARS = 6000


@dataclass
class FixResult:
    path: Path
    changed: int
    total: int
    changes: list[str] = field(default_factory=list)
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
    for chunk in _chunks(texts):
        numbered = "\n".join(f"[{i}] {texts[i]}" for i in chunk)
        response = await llm.complete(
            system=(
                "You are a meticulous editor. Apply the instructions to the numbered paragraphs. Keep the author's "
                "voice, meaning, names, dates and facts exactly; never invent content; don't add em dashes. Return "
                'JSON only: {"edits": [{"i": <number>, "text": "<the full corrected paragraph>"}], "changes": '
                '["<short note of a notable fix>", ...]}. Include only paragraphs you changed, and at most 8 changes.'
            ),
            messages=[
                Message.user(
                    f"Instructions: {instructions or 'Fix grammar, spelling and punctuation.'}\n\n{numbered}"
                )
            ],
            max_tokens=8000,
        )
        cost += response.usage.cost_usd
        data = _parse(response.text)
        for e in data.get("edits", []):
            try:
                i = int(e["i"])
            except (KeyError, TypeError, ValueError):
                continue
            new = str(e.get("text", "")).strip()
            if i in chunk and new and new != texts[i].strip():
                edits[i] = new
        changes += [str(c) for c in data.get("changes", [])][:8]

    out = _write(path, texts, edits, handle)
    return FixResult(
        path=out,
        changed=len(edits),
        total=sum(1 for t in texts if t.strip()),
        changes=changes[:10],
        cost_usd=cost,
    )


def _write(path: Path, texts: list[str], edits: dict[int, str], handle) -> Path:
    stem = path.stem
    ext = path.suffix.lower()
    if ext == ".docx" and handle is not None:
        doc, paras = handle
        for i, new in edits.items():
            _set_text(paras[i], new)
        out = path.with_name(f"{stem} (fixed by Wisp).docx")
        doc.save(str(out))
        return out
    final = [edits.get(i, t) for i, t in enumerate(texts)]
    if ext == ".pdf":
        from docx import Document

        doc = Document()
        for t in final:
            doc.add_paragraph(t)
        out = path.with_name(f"{stem} (fixed by Wisp).docx")
        doc.save(str(out))
        return out
    out = path.with_name(f"{stem} (fixed by Wisp){path.suffix}")
    out.write_text("\n".join(final), encoding="utf-8")
    return out
