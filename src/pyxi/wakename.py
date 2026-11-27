"""What speech recognition makes of "Hey Pyxi" at the start of a request, put back as "Pyxi,".

Heard on one evening: "Your pixxit split my left screen" (read as a complaint, "you split
my screen", so Pyxi undid it), "Epic City can use, feed the screen", "Hey Pixie", "Pixig",
"Hello, Pixi". Only the very start of what was said is touched, and only when the name is a
form of address there: a sentence that merely mentions a pixie or Epic City later on stays as
it was, and so does one that starts with a pixie that is a thing ("Pixie cut ideas").

The other way round, heard on 13 November: "Can you create a Pyxi?" in a spreadsheet. They
said "pivot"; the speech model, told how Pyxi's name is spelled, wrote the name. Where the
name stands for a thing that is made in a sheet ("create a Pyxi", "a Pyxi table"), it is put
back as "pivot". The word "pivot" itself is never touched.
"""

from __future__ import annotations

import re

_NAME = r"(?:pyxi|pyxie|pixie|pixi|pixy|pixey|pixig|pixit|pixxit|pixxi|pixel?y|piksi|pigsy|picks?y|pixee)"
# After the name, these make it a thing they are talking about, not Pyxi being spoken to.
_A_THING = r"(?:cuts?|dust|haircuts?|hair|bobs?|wings?|costumes?|sticks?|hollow|lott|tables?|charts?|art|dreams?)"
_WAKE = re.compile(
    r"^\W*(?:(?:hey|hi|hello|yo|ok|okay|oi|so)[,.!]?\s+)?"
    r"(?:(?:your|you|you're)\s+)?"
    rf"(?:{_NAME}|epic\s+city|hey\s+pixie)\b(?!['’]s\b)(?!\s+{_A_THING}\b)[,.!]?\s*",
    re.IGNORECASE,
)

# "create a Pyxi", "make me a Pyxi table", "insert the Pyxi from this data": a pivot.
_HEARD_NAME = r"(?:pyxi|pyxie|pixi|pixie|pixy)"
_MAKES = r"(?:create|creates|creating|make|makes|making|insert|add|build|generate|do|need|want|give me|set up)"
_PIVOT_TABLE = re.compile(rf"\b{_HEARD_NAME}(?=\s+(?:tables?|charts?)\b)", re.IGNORECASE)
_PIVOT_MADE = re.compile(
    rf"(\b{_MAKES}\s+(?:me\s+|us\s+)?(?:a|an|the|another|one more)\s+){_HEARD_NAME}\b"
    r"(?=\s*(?:[?.!,]|$|\s(?:for|of|from|with|on|in|out of|using|here|please|now)\b))",
    re.IGNORECASE,
)


def unmishear(text: str) -> str:
    """'Can you create a Pyxi?' -> 'Can you create a pivot?'. Only the name standing where a
    thing to make stands; "pivot" itself, and the name as a form of address, are left alone."""
    text = _PIVOT_TABLE.sub("pivot", text)
    return _PIVOT_MADE.sub(lambda m: m.group(1) + "pivot", text)


def normalize(text: str) -> str:
    """'Your pixxit split my left screen' -> 'Pyxi, split my left screen'."""
    m = _WAKE.match(text)
    if not m:
        return unmishear(text)
    rest = unmishear(text[m.end():].lstrip(" ,."))
    return f"Pyxi, {rest}" if rest else "Pyxi?"
