"""Filling a PDF form by asking: Pixel reads the form once, asks for the answers on one card,
and what is typed there goes straight into the matching boxes.

The first version let a model decide which answer went in which box each time. It was right
on the forms tried, but its reading could differ from run to run, tick boxes that belong
together were not one choice, and nobody saw what would be written until it was.

Now there are three parts, and a model is in only the first:

1. The map. The model is shown the page as it reads (pixel_forms.py) and writes one short line
   for each thing the form asks: a place to write, or a set of tick boxes that are one choice.
   It sees the form's printed words only, never what is typed in it and never an answer.
   Every line is checked against the page before it is believed: the field must exist and be
   the right kind, be used once, a tick box's option must be the words printed beside that
   box, and a question must share a word with what is printed around its field. A line that
   fails is dropped, so a box Pixel is unsure of is left empty, not guessed at. The map is
   kept on this PC by the form's fingerprint: the same form is never read twice.
2. The card. Every question, in the form's own order, with what is already known typed in:
   what the form holds now, and the obvious things from saved details (name, email, phone,
   date of birth, address). The rest is for the person to type. Nothing is written until they
   press the button.
3. The fill. Each answer goes to the field its question was checked against. One choice ticks
   one box and clears the others. No model is involved, and a box that asks for an ID number,
   a card number or a password is never on the card.

If the model can't be reached, the map is made from the page alone (each place to write by
the words beside it, each box by itself), so a form can still be filled.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from dataclasses import field as _list
from pathlib import Path
from typing import Any

from pyxi.agents.pixel_forms import Field, Form, junk_name

log = logging.getLogger(__name__)

VERSION = 2  # of the map's shape: a map made by an older reader is made again
MAX_VALUE = 2000
ENOUGH = 0.5  # a reading that accounts for less of the form than this is made again, and not kept
# What Pixel may fill from what is saved on this PC. The first six are the saved details in
# Settings; an address is kept for paper forms only (it is never part of a booking).
DETAILS = ("first_name", "last_name", "full_name", "email", "phone", "date_of_birth", "city",
           "address", "postal_code")  # fmt: skip
FORM_ONLY = ("address", "postal_code")
MEMORY_FILE = "form_details.json"
_STOP = frozenset(
    ["the", "and", "for", "your", "you", "are", "please", "print", "with", "from", "that",
     "this", "what", "which", "any", "all", "number", "name", "date", "other", "yes"]
)  # fmt: skip


@dataclass
class Question:
    id: str
    text: str
    kind: str  # "text", "choice" (one of) or "multi" (any of)
    section: str = ""
    field: str = ""  # a place to write: its field
    options: list[list[str]] = _list(default_factory=list)  # tick boxes: [words, field]
    detail: str = ""  # the saved detail that answers it, if it asks for the person themself


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def fingerprint(form: Form) -> str:
    """The same blank form gives the same print whoever fills it in and whatever is typed."""
    h = hashlib.sha256(f"v{VERSION}".encode())
    for f in form.fields:
        h.update(f"{f.name}|{f.kind}|{f.page}|{[round(v) for v in f.rect]}\n".encode())
    for page in form.pages:
        h.update(re.sub(r" filled\]", "]", page).encode())
    return h.hexdigest()[:24]


# ---------------------------------------------------------------------------- the map
SYSTEM = (
    "You turn a paper form into the short list of questions it asks. You are shown each page "
    "as it reads, top to bottom. Every place to write is marked with its own id in square "
    "brackets (an f and a number), and every tick box with its id and the word box. A box's "
    "words come right after it. A place to write belongs to the words before it, or to the "
    "words under its line, or, in a table, to the heading over its column.\n\n"
    "Write one line for each thing the form asks, in the form's order. ID stands for the id "
    "from the page, copied exactly:\n"
    "# Section name          (when a new part of the form starts)\n"
    "T ID | Question         (a place to write)\n"
    "T ID | Question | detail   (when it asks for something about the person filling the form: "
    "first_name, last_name, full_name, email, phone, date_of_birth, address, postal_code, city)\n"
    "C Question | Words=ID; Words=ID   (tick boxes where one is chosen; a box on its own is "
    "C Question | Yes=ID)\n"
    "M Question | Words=ID; Words=ID   (tick boxes where several can be chosen)\n\n"
    "Rules. Every id on the page gets a line, whoever would fill it in, except signatures and "
    "anything marked for office or staff use. Keep the form's own words and keep questions short. Say whose it is when the "
    "section decides that: 'Landlord phone number', 'Emergency contact email', 'Household "
    "member 2: date of birth'. Keep a format the form asks for, like (MM/DD/YYYY). The words "
    "of an option are the words printed beside that box. Give a detail only for the person's "
    "own answer, never for a landlord, employer, relative, co-applicant or contact. Use each "
    "id once. Never write an answer, and write nothing but these lines."
)

_T = re.compile(r"^T\s+\[?(f\d+)\b[^|]*\|\s*(.+?)\s*(?:\|\s*([a-z_]+)\s*)?$")
_CM = re.compile(r"^([CM])\s+(.+?)\s*\|\s*(.+)$")
_OPTION = re.compile(r"^(.*?)\s*=\s*\[?(f\d+)\b.*$")


def prompt(form: Form) -> str:
    """The blank form: which fields hold something already is the person's business, and a
    map is for every copy of the form, so the 'filled' marks are taken off."""
    return "The form:\n" + re.sub(r" filled\]", "]", form.outline())[:70000]


def coverage(questions: list[Question], form: Form) -> float:
    """How much of the form the map accounts for, 0 to 1."""
    if not form.fields:
        return 0.0
    covered = {q.field for q in questions if q.field} | {o[1] for q in questions for o in q.options}
    return len(covered) / len(form.fields)


def _around(form: Form, f: Field, above: int = 9, below: int = 1) -> str:
    """The words printed on a field's line and the lines just above it, run together."""
    lines = form.pages[f.page].split("\n") if f.page < len(form.pages) else []
    near = lines[max(0, f.line - above) : f.line + below + 1]
    return _norm(re.sub(r"\[f\d+[^\]]*\]", " ", " ".join(near)))


def _anchored(question: str, form: Form, f: Field) -> bool:
    """Does the question share a word with what is printed around its field? A line the model
    made up, or pinned to the wrong part of the page, shares none."""
    words = {w for w in re.findall(r"[a-z]{3,}", question.lower()) if w not in _STOP}
    if not words:
        words = set(re.findall(r"[a-z]{3,}", question.lower()))
    near = _around(form, f)
    if any(w in near for w in words) or (not near and bool(words)):
        return True
    # "Other (specify)" beside "Other __": an everyday word counts when it is on the field's
    # own line, where it can only be about this field.
    own = _around(form, f, above=0, below=0)
    return any(w in own for w in re.findall(r"[a-z]{3,}", question.lower()))


def _elsewhere(question: str, form: Form, f: Field) -> bool:
    """Does the question fit a neighbour on the same line better than the field it was given?
    Two boxes side by side (a signature and its date, the columns of a table) are told apart
    by the words printed over or under each one. The words before a box count as its own
    only when every box on the line has some ("Position: [ ] Hours: [ ]"); otherwise they
    name the whole row ("Full time employment: $ [ ] $ [ ]") and tell nothing apart."""
    known = form.by_id()
    mates = [known[m] for m in f.mates if m in known]
    if not mates:
        return False
    own_before = all(re.search(r"[A-Za-z]{3}", x.before) for x in (f, *mates))
    asked = set(re.findall(r"[a-z]{3,}", question.lower()))

    def fits(x: Field) -> int:
        near = _norm(" ".join([x.above, x.below, x.before if own_before else ""]))
        return sum(1 for w in asked if w in near)

    mine = fits(f)
    return any(fits(m) > mine for m in mates)


def _same_words(said: str, printed: str) -> bool:
    a, b = _norm(said), _norm(printed)
    return bool(a) and bool(b) and (a in b or b in a)


def read_map(answer: str, form: Form) -> tuple[list[Question], int]:
    """The model's lines as questions, each checked against the page, and how many lines
    were dropped because they did not hold up."""
    known = {f.id: f for f in form.fields}
    used: set[str] = set()
    out: list[Question] = []
    dropped = 0
    section = ""
    for raw in (answer or "").splitlines():
        line = raw.strip().lstrip("-*").strip()
        if not line:
            continue
        if line.startswith("#"):
            section = " ".join(line.lstrip("# ").split())[:60]
            continue
        if m := _T.match(line):
            fid, text, detail = m.group(1), " ".join(m.group(2).split())[:120], m.group(3) or ""
            f = known.get(fid)
            if f is None or f.kind != "text" or fid in used or not _anchored(text, form, f):
                dropped += 1
                continue
            if _elsewhere(text, form, f):
                dropped += 1
                continue
            used.add(fid)
            out.append(Question("", text, "text", section, fid, [],
                                detail if detail in DETAILS else ""))  # fmt: skip
            continue
        if m := _CM.match(line):
            options: list[list[str]] = []
            parts = [p.strip() for p in m.group(3).split(";") if p.strip()]
            for part in parts:
                o = _OPTION.match(part)
                f = known.get(o.group(2)) if o else None
                words = " ".join(o.group(1).split())[:60] if o else ""
                # A box on its own is "Yes": its words are the question, checked like one.
                alone = len(parts) == 1 and words.lower() in ("yes", "x", "tick", "checked", "")
                if f is None or f.kind != "box" or f.id in used:
                    dropped += 1
                    continue
                if not (_same_words(words, f.label) or (alone and _anchored(m.group(2), form, f))):
                    dropped += 1
                    continue
                used.add(f.id)
                options.append([words or "Yes", f.id])
            if options:
                kind = "multi" if m.group(1) == "M" and len(options) > 1 else "choice"
                out.append(Question("", " ".join(m.group(2).split())[:120], kind, section, "", options))
            continue
        dropped += 1
    for i, q in enumerate(out, 1):
        q.id = f"q{i}"
    return out, dropped


def plain_map(form: Form) -> list[Question]:
    """A map from the page alone, when no model can be asked: each place to write by its own
    name or the words beside it, each box by itself."""
    out: list[Question] = []
    for f in form.fields:
        words = f.name.rsplit(".", 1)[-1] if not junk_name(f.name) else f.label
        words = re.sub(r"[\s:$_*]+$", "", " ".join(words.split()))[:120]
        if not re.search(r"[A-Za-z]{2}", words):
            continue
        if f.kind == "text":
            from pyxi.agents.pixel_pdf import detail_key

            out.append(Question("", words, "text", f"Page {f.page + 1}", f.id, [], detail_key(words)))
        else:
            out.append(Question("", words, "choice", f"Page {f.page + 1}", "", [["Yes", f.id]]))
    for i, q in enumerate(out, 1):
        q.id = f"q{i}"
    return out


def load_map(data_dir: Path, key: str) -> list[Question] | None:
    try:
        raw = json.loads((Path(data_dir) / "forms" / f"{key}.json").read_text(encoding="utf-8"))
        if raw.get("version") != VERSION:
            return None
        return [Question(**q) for q in raw["questions"]]
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_map(data_dir: Path, key: str, questions: list[Question]) -> None:
    """Only how the form reads is kept: its questions and which box each belongs to. Never an
    answer."""
    try:
        folder = Path(data_dir) / "forms"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{key}.json").write_text(
            json.dumps({"version": VERSION, "questions": [asdict(q) for q in questions]}),
            encoding="utf-8",
        )
    except OSError:
        log.info("pixel: couldn't keep the form's map", exc_info=True)


# ---------------------------------------------------------------------------- what they said in chat
SAID_SYSTEM = (
    "A person gave some answers for a form. Match each answer to the one question it answers. "
    "Write one line for each match, 'q12 = the answer', using their words (for a choice, one of "
    "its options exactly; for several, options separated by ';'). Leave out an answer no "
    "question asks for, and never match on a guess. Write nothing but these lines."
)


def said_prompt(questions: list[Question], said: dict[str, Any]) -> str:
    lines = []
    for q in questions:
        opts = f" [{' / '.join(o[0] for o in q.options)}]" if q.options else ""
        lines.append(f"{q.id} | {q.section} | {q.text}{opts}")
    return f"Their answers: {json.dumps(said, ensure_ascii=False)[:6000]}\n\nThe questions:\n" + "\n".join(lines)


def read_said(answer: str, questions: list[Question]) -> dict[str, Any]:
    by = {q.id: q for q in questions}
    out: dict[str, Any] = {}
    for line in (answer or "").splitlines():
        m = re.match(r"^\W*(q\d+)\s*=\s*(.+?)\s*$", line)
        q = by.get(m.group(1)) if m else None
        if q is None:
            continue
        value = _answer(q, [v.strip() for v in m.group(2).split(";")] if q.kind == "multi" else m.group(2))
        if value not in ("", [], None):
            out[q.id] = value
    return out


# ---------------------------------------------------------------------------- the card
def secret_question(q: Question, form: Form, secret: re.Pattern[str]) -> bool:
    known = {f.id: f for f in form.fields}
    fields = [known[i] for i in ([q.field] if q.field else [o[1] for o in q.options]) if i in known]
    return bool(secret.search(q.text)) or any(secret.search(f.name) or secret.search(f.label) for f in fields)


def card(
    questions: list[Question],
    form: Form,
    details: dict[str, str],
    said: dict[str, Any],
    secret: re.Pattern[str],
) -> list[dict[str, Any]]:
    """What the card shows: each question with what is already known typed in. Built on this
    PC, from the form's own values and the saved details; none of it goes to a model."""
    known = {f.id: f for f in form.fields}
    full = " ".join(x for x in (details.get("first_name", ""), details.get("last_name", "")) if x)
    have = {**details, "full_name": full}
    # A saved detail answers one question, the first that asks for it ("Current address", not
    # "Address you are moving to" as well); a phone number goes to the mobile line if there is one.
    takes: dict[str, str] = {}
    for q in questions:
        if q.kind == "text" and q.detail and have.get(q.detail):
            mobile = q.detail == "phone" and re.search(r"(?i)cell|mobile", q.text)
            if q.detail not in takes or (mobile and not re.search(r"(?i)cell|mobile", takes.get("phone:words", ""))):
                takes[q.detail] = q.id
                if q.detail == "phone":
                    takes["phone:words"] = q.text
    out: list[dict[str, Any]] = []
    for q in questions:
        if secret_question(q, form, secret):
            continue
        item: dict[str, Any] = {"id": q.id, "label": q.text, "section": q.section, "kind": q.kind}
        if q.kind == "text":
            f = known.get(q.field)
            if f is None:
                continue
            item["page"] = f.page + 1
            item["value"], item["from"] = f.value, "form" if f.value else ""
            if not f.value and q.id in said:
                item["value"], item["from"] = str(said[q.id]), "said"
            elif not f.value and takes.get(q.detail) == q.id:
                item["value"], item["from"] = have[q.detail], "saved"
        else:
            boxes = [(words, known[fid]) for words, fid in q.options if fid in known]
            if not boxes:
                continue
            item["page"] = boxes[0][1].page + 1
            item["options"] = [words for words, _ in boxes]
            ticked = [words for words, f in boxes if f.filled]
            picked = said.get(q.id)
            if picked not in (None, "", []):
                ticked = picked if isinstance(picked, list) else [picked]
                item["from"] = "said"
            else:
                item["from"] = "form" if ticked else ""
            item["value"] = ticked if q.kind == "multi" else (ticked[0] if ticked else "")
        out.append(item)
    return out


def _answer(q: Question, value: Any) -> Any:
    """One answer the way its question takes it, or nothing if it isn't one."""
    if q.kind == "text":
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            return None
        return " ".join(str(value).split())[:MAX_VALUE]
    by = {_norm(o[0]): o[0] for o in q.options}
    if q.kind == "multi":
        items = value if isinstance(value, list) else [value]
        return [by[_norm(v)] for v in items if isinstance(v, str) and _norm(v) in by]
    if value in ("", None, False):
        return ""
    if value is True and len(q.options) == 1:
        return q.options[0][0]
    return by.get(_norm(value)) if isinstance(value, str) else None


def plan(
    questions: list[Question], form: Form, answers: dict[str, Any], secret: re.Pattern[str]
) -> dict[str, str]:
    """The card's answers as {the PDF's field name: what to write}. Only what a question on
    the card can take, only to the field that question was checked against."""
    known = {f.id: f for f in form.fields}
    placed: dict[str, str] = {}
    for q in questions:
        if q.id not in answers or secret_question(q, form, secret):
            continue
        value = _answer(q, answers[q.id])
        if value is None:
            continue
        if q.kind == "text":
            f = known.get(q.field)
            # An answer left as the card showed it is not written again: a long note with line
            # breaks in it would come back flattened, though nobody touched it.
            if f is not None and value != " ".join(f.value.split())[:MAX_VALUE]:
                placed[f.name] = value
            continue
        chosen = {_norm(v) for v in (value if isinstance(value, list) else [value]) if v}
        for words, fid in q.options:
            f = known.get(fid)
            if f is None:
                continue
            if _norm(words) in chosen:
                placed[f.name] = f.state  # one name shared by a set of boxes takes the chosen one
            elif f.name not in placed:
                placed[f.name] = "/Off"
    return placed


# ---------------------------------------------------------------------------- remembered on this PC
def remembered(data_dir: Path) -> dict[str, str]:
    """An address typed on an earlier form, kept for paper forms only."""
    try:
        raw = json.loads((Path(data_dir) / MEMORY_FILE).read_text(encoding="utf-8"))
        return {k: str(v)[:200] for k, v in raw.items() if k in FORM_ONLY and str(v).strip()}
    except (OSError, ValueError, AttributeError):
        return {}


def remember(
    data_dir: Path, questions: list[Question], answers: dict[str, Any], details: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """What the card's answers add to what is saved: (details missing from Settings, to be
    saved there; the address for paper forms, saved here). Nothing saved is ever replaced
    from a form: only a blank is filled."""
    new_details: dict[str, str] = {}
    new_forms = remembered(data_dir)
    was = dict(new_forms)
    for q in questions:
        value = answers.get(q.id)
        if q.kind != "text" or not q.detail or not isinstance(value, str) or not value.strip():
            continue
        value = " ".join(value.split())[:200]
        if q.detail in FORM_ONLY:
            new_forms[q.detail] = value
        elif q.detail != "full_name" and not details.get(q.detail):
            new_details[q.detail] = value
    if new_forms != was:
        try:
            Path(data_dir).mkdir(parents=True, exist_ok=True)
            (Path(data_dir) / MEMORY_FILE).write_text(json.dumps(new_forms, indent=1), encoding="utf-8")
        except OSError:
            log.info("pixel: couldn't keep the address for next time", exc_info=True)
    return new_details, new_forms
