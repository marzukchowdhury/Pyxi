"""Dictation: hold Ctrl + Windows, talk, let go, and the words are typed where the cursor is.

Speech-to-text is Pyxi's own (faster-whisper on this PC, or the listening model for Bengali), so
dictating costs nothing and needs no model call. This module only tidies what Whisper heard:
"um" and "uh" go, "new line" and "new paragraph" become line breaks, and spacing is fixed so
one dictation runs on from the last.
"""

from __future__ import annotations

import re

# Hesitations nobody means to type. Only the unambiguous ones: "like" and "you know" can be
# real words. Captures the comma before and the punctuation after, to tidy around them.
_FILLER = re.compile(r"(?i)(,\s*)?\b(?:um+|uh+|erm+|er|ah|hmm+|mm+)\b(\s*[,.!?])?\s*")
# Spoken layout, with Whisper's own punctuation right after the words.
_PARAGRAPH = re.compile(r"(?i)\s*\bnew paragraph\b[,.!:]?\s*")
_LINE = re.compile(r"(?i)\s*\b(?:new line|next line)\b[,.!:]?\s*")
# What Whisper "hears" in silence or noise. ("Thank you." is a real thing to dictate, so it stays.)
_NOTHING = re.compile(r"(?i)^\W*(?:thanks for watching!?|you|\.+|\[[^\]]*\]|\([^)]*\))\W*$")
_SENTENCE_START = re.compile(r"([.!?]\s+|^)([a-z])")


def _filler(m: re.Match[str]) -> str:
    before, after = m.group(1), (m.group(2) or "").strip()
    if after in (".", "!", "?"):
        return after + " "  # "the summary, um." -> "the summary."
    if before and after == ",":
        return " "  # "we should, uh, ship" -> "we should ship"
    if before or after == ",":
        return ", "  # "so, um I think" -> "so, I think"
    return " "


def clean(text: str) -> str:
    """What to type for what was said. Empty when nothing real was said."""
    text = " ".join(text.split())
    if not text or _NOTHING.match(text):
        return ""
    text = _FILLER.sub(_filler, text)
    text = _PARAGRAPH.sub("\n\n", text)
    text = _LINE.sub("\n", text)
    text = re.sub(r"[ \t]+([,.!?;:])", r"\1", text)  # "word ," -> "word,"
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"^[, \t]+", "", text)  # a filler at the very start left a comma
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text).strip(" ")
    if not text.strip(" \n,"):
        return "\n" * min(text.count("\n"), 2)  # just "new line": the line break itself
    lines = [_SENTENCE_START.sub(lambda m: m.group(1) + m.group(2).upper(), ln) for ln in text.split("\n")]
    text = "\n".join(lines)
    return text if text.endswith("\n") else text + " "  # the next dictation follows on
