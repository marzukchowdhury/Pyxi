"""Keys and tokens that happen to be on screen never leave the PC.

Password fields are masked by role, but a secret shown as plain text (a .env file in Notepad,
a dashboard that reveals an API key, a token pasted into a chat) has no role to go by. So text
that looks like a credential is hidden from the model's text, and its spot is blacked out of
the screenshot itself, before anything is sent or saved.
"""

from __future__ import annotations

import re

_PATTERNS = [
    r"sk-(?:or-v1-|ant-[a-z0-9]+-|proj-)?[A-Za-z0-9_\-]{20,}",  # OpenRouter, Anthropic, OpenAI
    r"AKIA[0-9A-Z]{16}",  # AWS access key
    r"AIza[0-9A-Za-z_\-]{30,}",  # Google API key
    r"gh[pousr]_[A-Za-z0-9]{30,}",  # GitHub tokens
    r"xox[abprs]-[A-Za-z0-9-]{10,}",  # Slack
    r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}",  # Stripe
    r"eyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{10,}",  # JWTs
    r"pyxi_[0-9a-f]{40,}",  # Pyxi's own beta token
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    # KEY=value lines in env files and configs: API_KEY=..., client_secret: ..., TOKEN="..."
    r"\b[A-Za-z0-9_]*(?i:api_?key|secret|token|password|passwd)[A-Za-z0-9_]*\s*[=:]\s*['\"]?[^\s'\"]{8,}",
]
_SECRET = re.compile("|".join(f"(?:{p})" for p in _PATTERNS))
PATTERN = _SECRET  # also scrubs logs (pyxi/logscrub.py)

HIDDEN = "(hidden: looks like a key or token)"


def looks_secret(text: str | None) -> bool:
    return bool(text) and _SECRET.search(text) is not None
