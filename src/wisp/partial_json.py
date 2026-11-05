"""Read a tool call's JSON while it is still streaming in.

Lets Wisp act on a reply before it's finished: say the first sentence of `say` the moment it's
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


def first_chunk(text: str, max_words: int = 14) -> str:
    """What the voice can start on: the first sentence, or for a long one, the words up to its
    first natural pause (a comma after six or more words), so speech begins sooner."""
    whole = finished_sentences(text)
    if whole and len(whole.split()) <= max_words:
        return whole
    for m in _PAUSE.finditer(text):
        head = text[: m.end()].strip()
        if 6 <= len(head.split()) <= max_words + 4:
            return head
    return whole
