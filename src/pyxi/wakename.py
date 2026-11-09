"""What speech recognition makes of "Hey Pyxi" at the start of a request, put back as "Pyxi,".

Heard on one evening: "Your pixxit split my left screen" (read as a complaint, "you split
my screen", so Pyxi undid it), "Epic City can use, feed the screen", "Hey Pixie", "Pixig",
"Hello, Pixi". Only the very start of what was said is touched, so a sentence that merely
mentions a pixie or Epic City later on stays as it was.
"""

from __future__ import annotations

import re

_NAME = r"(?:pyxi|pyxie|pixie|pixi|pixy|pixey|pixig|pixit|pixxit|pixxi|pixel?y|piksi|pigsy|picks?y|pixee)"
_WAKE = re.compile(
    r"^\W*(?:(?:hey|hi|hello|yo|ok|okay|oi|so)[,.!]?\s+)?"
    r"(?:(?:your|you|you're)\s+)?"
    rf"(?:{_NAME}|epic\s+city|hey\s+pixie)\b[,.!]?\s*",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """'Your pixxit split my left screen' -> 'Pyxi, split my left screen'."""
    m = _WAKE.match(text)
    if not m:
        return text
    rest = text[m.end():].lstrip(" ,.")
    return f"Pyxi, {rest}" if rest else "Pyxi?"
