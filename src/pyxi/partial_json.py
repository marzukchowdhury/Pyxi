"""Read a tool call's JSON while it is still streaming in.

Lets Pyxi act on a reply before it's finished: say the first sentence of `say` the moment it's
written, and show (and talk through) each cue-card point as soon as that point is complete,
instead of waiting for the whole card. Only complete pieces are ever returned.
"""

from __future__ import annotations

import json
import re
from typing import Any


def partial_string(src: str, key: str) -> tuple[str, bool] | None:
    """The value of string field ``key`` so far, and whether it's finished. None if not started."""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*"', src)
    if not m:
        return None
    out, i, escaped = [], m.end(), False
    while i < len(src):
        ch = src[i]
        if escaped:
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/"}.get(ch, ch))
            escaped = False
        elif ch == "\\":
            if i + 1 < len(src) and src[i + 1] == "u":
                if i + 5 >= len(src):
                    break  # the escape isn't all here yet
                try:
                    out.append(chr(int(src[i + 2 : i + 6], 16)))
                except ValueError:
                    pass
                i += 6
                continue
            escaped = True
        elif ch == '"':
            return "".join(out), True
        else:
            out.append(ch)
        i += 1
    return "".join(out), False


def complete_items(src: str, key: str = "items") -> list[dict[str, Any]]:
    """The objects of array ``key`` that have fully arrived, in order."""
    m = re.search(rf'"{re.escape(key)}"\s*:\s*\[', src)
    if not m:
        return []
    items: list[dict[str, Any]] = []
    depth, start, in_str, escaped = 0, -1, False, False
    for i in range(m.end(), len(src)):
        ch = src[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(src[start : i + 1])
                    if isinstance(obj, dict):
                        items.append(obj)
                except ValueError:
                    pass
                start = -1
        elif ch == "]" and depth == 0:
            break
    return items


_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
_PAUSE = re.compile(r"[,;:\u2014](?:\s)")


def finished_sentences(text: str, min_words: int = 4) -> str:
    """The leading part of ``text`` made of whole sentences (at least ``min_words`` words), or ''."""
    for m in _SENTENCE_END.finditer(text):
        head = text[: m.end()].strip()
        if len(head.split()) >= min_words and not head.endswith(
            ("e.g.", "i.e.", "vs.", "Mr.", "Dr.", "St.")
        ):
            return head
    return ""


def first_chunk(text: str, max_words: int = 24) -> str:
    """What the voice can start on: the first whole sentence(s). A sentence is only split at a
    pause (a comma) when it's already very long, because a clause said on its own ("filtered
    to open now, ranked by the reviews.") sounds like a separate instruction, not the end of
    what Pyxi was saying."""
    whole = finished_sentences(text)
    if whole and len(whole.split()) <= max_words:
        return whole
    if whole:  # one very long sentence: start on it at its first pause
        text = whole
    elif len(text.split()) <= max_words:
        return ""  # not finished yet, and not long enough to split: wait for its end
    for m in _PAUSE.finditer(text):
        head = text[: m.end()].strip()
        if 10 <= len(head.split()) <= max_words:
            return head
    return whole


_STYLE = re.compile(r'"style"\s*:\s*"on_picture"')
_PICTURE = re.compile(r'"picture"\s*:\s*\[\s*([-\d.eE+]+(?:\s*,\s*[-\d.eE+]+){3})\s*\]')
_WINDOW = re.compile(r'"picture_window"\s*:\s*true')
_PICTURE_ID = re.compile(r'"picture_element_id"\s*:\s*(\d+)\s*[,}]')


def picture_head(src: str, on_picture: bool = False) -> dict[str, Any] | None:
    """From a streaming explain_on_screen call: the picture being explained, once its style and
    box (or element id) have fully arrived, so its parts can be found while the rest streams."""
    if not on_picture and not _STYLE.search(src):
        return None
    head: dict[str, Any] = {"style": "on_picture"}
    if m := _PICTURE.search(src):
        head["picture"] = [float(v) for v in m.group(1).split(",")]
    elif m := _PICTURE_ID.search(src):
        head["picture_element_id"] = int(m.group(1))
    elif _WINDOW.search(src):
        head["picture_window"] = True
    else:
        return None
    return head
