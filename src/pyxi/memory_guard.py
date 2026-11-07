"""Memory hygiene: passwords, card numbers, government IDs and API keys are never remembered.

Pyxi (``remember``) and its teammates (Otto's own ``remember`` tool, in Scout's runs) both write
to an otto MemoryStore. :func:`install` puts a guard on the store itself, so neither can save
anything that looks like a secret: the write is refused with a reason the model sees.

The same patterns as the screen's secret blackout (screen/secrets.py), plus card numbers (with
their check digit), US Social Security and Canadian Social Insurance numbers, and "my password
is ..." / passport, licence and health card numbers mentioned in words.
"""

from __future__ import annotations

import re

from pyxi.screen import secrets


class SensitiveMemory(ValueError):
    """Refused: it looks like a password, a card number, an ID number or a key."""


# As cards are written: 4-4-4-4(-3) or Amex's 4-6-5, one kind of separator, or no separator.
# (An order number like 702-8812345-1234567 isn't grouped like a card.)
_CARD = re.compile(
    r"(?<![\d-])(?:\d{4}([ -]?)\d{4}\1\d{4}\1\d{1,7}|\d{4}([ -]?)\d{6}\2\d{4,5})(?![\d-])"
)
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_SIN = re.compile(r"(?<!\d)\d{3}[ -]\d{3}[ -]\d{3}(?!\d)")
_SAID = re.compile(
    r"\b(?:pass(?:word|code|phrase)|pwd|pin|cvv|cvc|security code|one[- ]time code|otp)\b"
    r"\s*(?:is|was|=|:|-)?\s*\S{3,}",
    re.I,
)
_ID = re.compile(
    r"\b(?:passport|driver'?s? licen[cs]e|licen[cs]e number|health card|ohip|msp|ssn|sin|"
    r"social (?:security|insurance)|national id|tax id|nid)\b[^.\n]{0,25}?[A-Z0-9][A-Z0-9 -]{5,}",
    re.I,
)


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0


def why_sensitive(text: str) -> str | None:
    """What kind of secret this looks like, or None."""
    if not text:
        return None
    if secrets.looks_secret(text):
        return "an API key or token"
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return "a card number"
    if _SSN.search(text):
        return "a Social Security number"
    for m in _SIN.finditer(text):
        if _luhn(re.sub(r"\D", "", m.group(0))):
            return "a Social Insurance number"
    if _SAID.search(text):
        return "a password or code"
    if _ID.search(text):
        return "an ID number"
    return None


def check(key: str, content: str) -> None:
    why = why_sensitive(f"{key}: {content}")
    if why:
        raise SensitiveMemory(
            f"That looks like {why}. Pyxi never saves passwords, card numbers, ID numbers or keys "
            "in its memory, so it wasn't remembered."
        )


_installed = False


def install() -> None:
    """Guard every otto MemoryStore in this process (Pyxi's and the teammates')."""
    global _installed
    if _installed:
        return
    from otto.storage import MemoryStore

    original = MemoryStore.remember

    def remember(self, scope, key, content, *args, **kwargs):  # type: ignore[no-untyped-def]
        check(str(key), str(content))
        return original(self, scope, key, content, *args, **kwargs)

    MemoryStore.remember = remember  # type: ignore[method-assign]
    _installed = True
