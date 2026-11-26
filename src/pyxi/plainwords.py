"""What went wrong, in words a person can act on.

Every failure the user is shown passes through here: a job's (``agents.jobs.describe_failure``)
and a turn's (``companion.brain.friendly_error``). ``explain`` knows the failures that have a
next step (sign in again, close the file, check the internet) and says each one the same way
everywhere. ``safe_line`` is the last resort for anything else: what is shown is either a plain
sentence or one stock line, never a Python error, a tuple or dict, a path with the Windows
user name in it, an address with a key in it, or a key.

A line may come with an ``action``: the button a ``job_ready`` card carries that mends it
(``"google"`` is Sign in to Google, see overlay/Notch.tsx).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from pyxi import account
from pyxi.connectors.google import GOOGLE_EXPIRED, GOOGLE_OUR_SIDE
from pyxi.logscrub import scrub

# ------------------------------------------------------------------ the lines
GOOGLE_PERMISSION = (
    "Google hasn't given Pyxi permission for that yet. Press Sign in to Google, tick every "
    "box Google shows, then ask me once more. The same button is in Settings under Connected "
    "accounts."
)
GOOGLE_NOT_YOURS = (
    "Google won't let this account open that file. It may belong to another Google account, "
    "or it was deleted. Open it in your browser to check, or share it with the account Pyxi "
    "is connected to, then ask again."
)
GOOGLE_REFUSED = (
    "Google won't let me do that with this account. Signing in to Google again, in Settings "
    "under Connected accounts, may fix it."
)
GOOGLE_BUSY = "Google is asking me to slow down. Try again in a minute."
GOOGLE_DOWN = "Google is having trouble right now. Try again in a minute."
GOOGLE_SLOW = "Google took too long to answer. Try again in a moment."
OFFLINE = "I can't get online right now. Check the internet connection, then ask me again."
MODEL_OFFLINE = "I can't reach the AI model. Check the internet connection, then ask me again."
MODEL_BUSY = "The AI model is busy right now. Try again in a minute."
MODEL_SLOW = "The AI model took too long to answer. Ask me again."
MODEL_KEY = "The AI model turned the key down. Check the model settings, then try again."
MODEL_KEY_BETA = (
    "The Pyxi beta didn't accept this PC's sign-in. Open Settings and type your invite code "
    "again, then ask me once more."
)
MODEL_CREDIT = (
    "The AI account has run out of credit. Add credit to the account your key belongs to, "
    "then ask me again."
)
MODEL_MISSING = (
    "The AI model Pyxi is set to use isn't available right now. Try again in a few minutes."
)
MODEL_TOO_MUCH = (
    "That was too much for the AI model to read in one go. Try a smaller file, or ask for "
    "one part of it at a time."
)
SLOW = "That took too long and I gave up waiting. Check the internet connection, then ask me again."
MODEL_DOWN = "The AI service is having trouble right now. That is not your PC. Try again in a few minutes."
BETA_DOWN = (
    "The Pyxi beta's AI service is down right now. That is on our side, not yours. Try again "
    "in a few minutes."
)
BUDGET_JOB = "I stopped before going over the spending limit for this job."
BROWSER_START = "I couldn't open a browser window on this PC. Restarting Pyxi usually fixes it."
BROWSER_CLOSED = (
    "The browser window was closed before I finished. Ask me again and leave it open until "
    "I'm done."
)
SITE_UNREACHABLE = "I couldn't reach that site. Is the internet connection up?"
SITE_FAILED = "That site wouldn't load."
SITE_SLOW = "The site took too long to respond."
DISK_FULL = "This PC's disk is full, so I couldn't save that. Free up some space, then ask again."
FILE_DAMAGED = (
    "That file won't open. It may be damaged, or locked with a password. Open it in its own "
    "app to check, save a fresh copy, then ask again."
)
PICTURE_DAMAGED = "That picture won't open. It may be damaged, or not really a picture. Try another copy of it."
PDF_DAMAGED = "That PDF won't open. It may be damaged. Try opening it yourself, save a fresh copy, then ask again."
PDF_LOCKED = "That PDF is locked with a password. Ask me to unlock it first, with the password."
OFFICE_MISSING = (
    "That needs Excel, Word or PowerPoint, and I couldn't start it on this PC. Open the file "
    "in the app yourself, then ask again."
)
OFFICE_REFUSED = (
    "The Office app didn't let me do that. If a box is open in it, or a cell is being typed "
    "in, finish that first, then ask again."
)
# The last resort. It says what to do, because nothing better is known.
UNKNOWN = "Something went wrong on my side and I couldn't finish. Ask me again, and if it keeps happening, restart Pyxi."

# Which lines come with a button on their card.
ACTIONS = {GOOGLE_EXPIRED: "google", GOOGLE_PERMISSION: "google"}
TITLES = {GOOGLE_EXPIRED: "Sign in to Google again", GOOGLE_PERMISSION: "Google needs your OK"}

# "Try again": the same request once more. The line only says the failure is the kind that
# passes (no internet, the model busy, a timeout, Google's rate limit). Whether the button
# may be shown is the brain's to decide, because it alone knows if anything was already
# done: it is never offered after something was sent, posted, paid or changed
# (companion/brain.py, ``_offer_retry``).
RETRY = "retry"
RETRY_LINES = frozenset({
    OFFLINE, MODEL_OFFLINE, MODEL_BUSY, MODEL_SLOW, MODEL_DOWN, BETA_DOWN, SLOW,
    GOOGLE_BUSY, GOOGLE_DOWN, GOOGLE_SLOW, SITE_UNREACHABLE, SITE_SLOW,
})  # fmt: skip
_RETRY_SAID = re.compile(
    r"^(?:LinkedIn|Discord) is (?:asking me to slow down|having trouble right now)\."
)
# "Open Settings": Pyxi's own Settings, at the part that mends it ("settings:files").
SETTINGS = "settings"
_SETTINGS_PART = (
    ("files", re.compile(r"(?i)\bunder Your files\b|search my files|file search is off")),
    (
        "accounts",
        re.compile(
            r"(?i)\bunder Connected accounts\b|\bisn't connected\b|\bConnect (?:Google|it) in Settings\b|"
            r"Connecting the account again in Settings"
        ),
    ),
    ("budget", re.compile(r"(?i)\bspending limit\b|today's budget is used up|used up today's budget")),
    ("voice", re.compile(r"(?i)\bVoice tab\b")),
)


def settings_part(line: str) -> str:
    """The part of Settings that mends this line ("files", "accounts", "budget", "voice"), or ""."""
    return next((part for part, said in _SETTINGS_PART if said.search(line or "")), "")


def action_for(line: str) -> str:
    """The card button that mends what this line says, or "". The Pyxi account's own lines
    (the trial or the plan ended, a payment failed, the allowance is used, signed out) carry
    theirs: "choose_plan", "see_plans", "update_card", "pyxi_sign_in" (pyxi/account.py).
    The rest: "google" (Sign in to Google), "retry" (Try again, see RETRY), and
    "settings:<part>" (Open Settings, at that part). One button for a line, never two."""
    line = (line or "").strip()
    if mend := ACTIONS.get(line, "") or account.action_for(line):
        return mend
    if part := settings_part(line):
        return f"{SETTINGS}:{part}"
    if line in RETRY_LINES or _RETRY_SAID.match(line):
        return RETRY
    return ""


def title_for(line: str) -> str:
    """The title of the card for a line that has a button, or ""."""
    line = (line or "").strip()
    return TITLES.get(line, "") or account.title_for(line)


@dataclass(frozen=True)
class Plain:
    line: str
    kind: str  # "google", "model", "net", "file", "browser", "office", "plan" (the Pyxi account)

    @property
    def action(self) -> str:
        return action_for(self.line)


_BETA_SAID = re.compile(
    r"(You've used today's Pyxi beta allowance[^\"'}]*|The Pyxi beta is at its limit[^\"'}]*|"
    r"Sign in to the Pyxi beta first[^\"'}]*)"
)
_NO_NET = re.compile(
    r"(?i)getaddrinfo failed|name or service not known|temporary failure in name resolution|"
    r"network is unreachable|no address associated|\[errno 1100[1-4]\]|"
    r"winerror 1005[01]|winerror 1006[05]|nodename nor servname|all connection attempts failed|"
    r"connection error\.?$|cannot connect to host|failed to establish a new connection"
)
_MODEL_TYPES = (
    "LLMError", "ModelRefusalError", "APIError", "APIStatusError", "APIConnectionError",
    "APITimeoutError", "RateLimitError", "AuthenticationError", "PermissionDeniedError",
    "NotFoundError", "BadRequestError", "InternalServerError", "OverloadedError",
    "UnprocessableEntityError", "ConflictError", "APIResponseValidationError",
)  # fmt: skip
_NET_TYPES = (
    "ConnectError", "ConnectTimeout", "NetworkError", "ClientConnectorError", "gaierror",
    "ConnectionError", "ConnectionRefusedError", "ConnectionResetError", "ConnectionAbortedError",
    "RemoteProtocolError", "ReadError", "WriteError", "ProxyError",
)  # fmt: skip
_TIMEOUT_TYPES = ("ReadTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException", "TimeoutError")
_FILE_TYPES = {
    "BadZipFile", "PackageNotFoundError", "InvalidFileException", "UnidentifiedImageError",
    "PdfReadError", "PdfStreamError", "EmptyFileError", "FileNotDecryptedError",
}  # fmt: skip
_COM_REFUSAL = re.compile(r"\(-2147\d{6},")


def _chain(exc: BaseException) -> list[BaseException]:
    """The exception and what caused it (a model error wraps the network one that explains it)."""
    seen: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and cur not in seen and len(seen) < 6:
        seen.append(cur)
        cur = cur.__cause__ or cur.__context__
    return seen


def _names(exc: BaseException) -> set[str]:
    return {k.__name__ for e in _chain(exc) for k in type(e).__mro__}


def _file_name(exc: BaseException, text: str) -> str:
    """The file an error is about, without its folders (which hold the Windows user name)."""
    for e in _chain(exc):
        name = getattr(e, "filename", None)
        if isinstance(name, (str, bytes, os.PathLike)) and name:
            return os.path.basename(os.fsdecode(name).replace("\\", "/").rstrip("/"))
    m = re.search(r"['\"]([^'\"\n]{1,240}[\\/])?([^'\"\\/\n]{1,120}\.\w{1,8})['\"]", text)
    return m.group(2) if m else ""


def _model(text: str, low: str, names: set[str], beta: bool) -> str:
    said = _BETA_SAID.search(text)
    if said:
        # The beta server says why in plain words, with when it resets: show exactly that.
        return said.group(1).strip()
    if "beta server has no model key" in low:
        return BETA_DOWN
    if "isn't part of the beta" in low:
        return MODEL_MISSING
    code = re.search(r"(?i)error code:\s*(\d{3})|\bstatus(?:[ _]code)?[:= ]+(\d{3})\b", text)
    status = int(next(g for g in code.groups() if g)) if code else 0
    if status == 401 or "AuthenticationError" in names or re.search(
        r"authentication|invalid (x-)?api.?key|no auth credentials|\b401\b", low
    ):
        return MODEL_KEY_BETA if beta else MODEL_KEY
    if status == 402 or re.search(r"insufficient credits|payment required|\b402\b", low):
        return BETA_DOWN if beta else MODEL_CREDIT
    if "APITimeoutError" in names or re.search(r"timed? ?out|timeout", low):
        return MODEL_SLOW
    if "APIConnectionError" in names or _NO_NET.search(text) or names & set(_NET_TYPES):
        return MODEL_OFFLINE
    if status in (429, 529) or "RateLimitError" in names or re.search(
        r"\b429\b|rate.?limit|overloaded|\b529\b", low
    ):
        return MODEL_BUSY
    if status == 413 or re.search(r"context length|maximum context|too many tokens|too large", low):
        return MODEL_TOO_MUCH
    if status == 404 or re.search(r"no endpoints found|model not found|not a valid model", low):
        return MODEL_MISSING
    if status >= 500 or "InternalServerError" in names or re.search(r"\b50[0234]\b", low):
        return MODEL_DOWN
    return ""


def _google(text: str, low: str) -> str:
    m = re.search(r"Google said (\d{3})", text)
    status = int(m.group(1)) if m else 0
    if status == 401 or "invalid credentials" in low:
        return GOOGLE_EXPIRED
    if status == 429 or re.search(r"ratelimitexceeded|rate limit exceeded|quota exceeded|too many requests", low):
        return GOOGLE_BUSY
    if status == 404 or (
        status == 403
        and re.search(r"does not have (sufficient )?permission|not found|cannot be (read|edited)", low)
    ):
        return GOOGLE_NOT_YOURS
    if status == 403 and re.search(r"insufficient|scope", low):
        return GOOGLE_PERMISSION
    if status == 403:
        return GOOGLE_REFUSED
    if status >= 500:
        return GOOGLE_DOWN
    return ""


def _service(name: str, status: int) -> str:
    """LinkedIn's or Discord's refusal ("LinkedIn said 401: {...}"), in words to act on."""
    if name == "Discord":
        if status == 401:
            return (
                "Discord turned down the bot key Pyxi was given. Put a fresh one in, the way "
                "Settings shows under Connected accounts, then restart Pyxi."
            )
        if status in (403, 404):
            return (
                "Discord won't let Pyxi's bot into that channel. Add the bot to the server and "
                "let it read and send messages there, then ask me again."
            )
    if status == 401:
        return (
            f"Your {name} sign-in has run out. Open Settings, press Connect next to {name} "
            "under Connected accounts, then ask me once more."
        )
    if status == 403:
        return (
            f"{name} won't let me do that with this account. Connecting {name} again, in "
            "Settings under Connected accounts, may fix it."
        )
    if status == 429:
        return f"{name} is asking me to slow down. Try again in a few minutes."
    if status >= 500:
        return f"{name} is having trouble right now. Try again in a minute."
    return ""


def connect_failure(name: str, exc: BaseException) -> str:
    """Why a sign-in from Settings (or a card's button) didn't connect, and what to press."""
    low = str(exc).lower()
    again = f"The {name} sign-in didn't go through. Press Connect to try again."
    if ".env" in low or ".pyxi" in low:
        return str(exc)[:220]  # a set-up step for someone running Pyxi from the code
    if "access_denied" in low or "denied" in low:
        return f"The {name} sign-in was cancelled, so nothing changed. Press Connect to try again."
    if "didn't finish" in low or "timed out" in low or "timeout" in low:
        return f"The {name} sign-in wasn't finished in time. Press Connect to try again."
    known = explain(exc)
    if known and known.kind == "net":
        return OFFLINE
    return safe_line(str(exc), again, 220)


OWN_TAB = (
    " Open that site in your own browser, sign in if it asks, then ask me again and I'll "
    "work in that tab."
)
_SHUT_OUT = re.compile(
    r"(?i)bot check|captcha|verify(?:ing)? (?:that )?you(?:'re| are) (?:a )?human|not a robot|"
    r"security verification|automated browsers?|blocked (?:me|the browser|automated)|access denied|"
    r"\bsign(?:ed|ing)?[ -]?in\b|\blog(?:ged|ging)?[ -]?in\b|needs? an account"
)


def own_tab_hint(summary: str) -> str:
    """The next step when a site kept the browser teammate out (its bot check, a sign-in
    only they can do): the same thing in a tab of their own browser. "" when the failure was
    something else, or the result already says what to do."""
    text = summary or ""
    if not _SHUT_OUT.search(text) or re.search(
        r"(?i)ask me (again|once more)|your own browser|Pyxi beta|in Settings", text
    ):
        return ""
    return OWN_TAB


HEARING_OFFLINE = (
    "I couldn't write down what you said: the listening pack for that language isn't on "
    "this PC yet and I can't get online to fetch it. Check the internet connection, then "
    "try again."
)
BENGALI_OFFLINE = (
    "Bengali listening needs the internet right now. Check the connection, then try again."
)


# Pyxi's own voice (about 350 MB, fetched on first start or from the Voice tab's button).
VOICE_PACK_LATER = (
    "Pyxi's own voice couldn't be downloaded just now, so the Windows voice is talking for "
    "it. Open the Voice tab and press Download to try again."
)
VOICE_PACK_OFFLINE = "The voice couldn't be downloaded. Check the internet connection, then press Download again."
VOICE_PACK_DISK = "This PC's disk is too full for the voice. Free up about 400 MB, then press Download again."
VOICE_PACK_IN_USE = "The voice is in use right now. Close Pyxi from the tray, open it again, then press Download again."
VOICE_PACK_FAILED = "The voice couldn't be downloaded just now. Press Download to try again in a minute."


def voice_pack_failure(exc: BaseException) -> str:
    """Why the voice pack didn't download, as one line for the Voice tab: what to do, never
    the download library's own words."""
    known = explain(exc)
    if known and known.line == DISK_FULL:
        return VOICE_PACK_DISK
    if isinstance(exc, PermissionError) or (known and known.kind == "file"):
        return VOICE_PACK_IN_USE
    names = _names(exc)
    if (known and known.kind == "net") or "broke off" in str(exc) or names & {
        "HTTPError", "TransportError", "StreamError", "HTTPStatusError",
    }:  # fmt: skip
        return VOICE_PACK_OFFLINE
    return VOICE_PACK_FAILED


def hearing_failure(exc: BaseException, fallback: str) -> str:
    """Why speech could not be turned into words, when the cause is one a person can mend.
    Bad audio (the usual cause) keeps the caller's own line."""
    text = str(exc)
    low = text.lower()
    if "bengali listening needs the internet" in low:
        return BENGALI_OFFLINE
    known = explain(exc)
    if known and known.kind in ("model", "net"):
        if known.line in (OFFLINE, MODEL_OFFLINE, SLOW, SITE_SLOW, SITE_UNREACHABLE, MODEL_SLOW):
            return "I couldn't write down what you said. " + OFFLINE
        return "I couldn't write down what you said. " + known.line
    if re.search(
        r"localentrynotfound|snapshot|huggingface|hf_hub|model files? (?:are|is) missing|"
        r"unable to open file 'model\.bin'|cannot find.*model|no such file.*model",
        low,
    ) or "LocalEntryNotFoundError" in _names(exc):
        return HEARING_OFFLINE
    if known and known.line == DISK_FULL:
        return "I couldn't write down what you said. " + DISK_FULL
    return fallback


def _file(exc: BaseException, text: str, low: str, names: set[str]) -> str:
    errno = next((getattr(e, "errno", None) for e in _chain(exc) if isinstance(e, OSError)), None)
    win = next((getattr(e, "winerror", None) for e in _chain(exc) if isinstance(e, OSError)), None)
    if errno == 28 or win in (39, 112) or re.search(
        r"no space left|not enough space on the disk|winerror 112\b|errno 28\b", low
    ):
        return DISK_FULL
    name = _file_name(exc, text) or "That file"
    if win in (32, 33) or re.search(r"winerror 3[23]\b|being used by another process", low):
        return f"{name} is open in another app. Close it there, then ask again."
    if "PermissionError" in names or errno == 13 or win == 5 or re.search(
        r"errno 13\b|winerror 5\b|permission denied|access is denied", low
    ):
        return (
            f"Windows won't let me open or save {name}. It may be open in another app, "
            "read-only, or in a folder Pyxi can't write to. Close it, or pick another folder, "
            "then ask again."
        )
    if "FileNotFoundError" in names or "NotADirectoryError" in names or re.search(
        r"errno 2\b|winerror [23]\b|no such file or directory|cannot find the (file|path)", low
    ):
        return f"I can't find {name}. It may have been moved or renamed. Tell me where it is now."
    if "FileNotDecryptedError" in names or "file has not been decrypted" in low:
        return PDF_LOCKED
    if names & {"PdfReadError", "PdfStreamError", "EmptyFileError"} or "eof marker not found" in low:
        return PDF_DAMAGED
    if "UnidentifiedImageError" in names or "cannot identify image file" in low:
        return PICTURE_DAMAGED
    if names & {"BadZipFile", "PackageNotFoundError", "InvalidFileException"} or re.search(
        r"file is not a zip file|package not found at|bad magic number for", low
    ):
        return FILE_DAMAGED
    return ""


def explain(exc: BaseException, *, beta: bool | None = None, job: bool = False) -> Plain | None:
    """A failure Pyxi knows, as a line that says what happened and what to do next. None for
    one it doesn't know (then :func:`safe_line` decides what may be shown)."""
    text = str(exc)
    low = text.lower()
    names = _names(exc)
    if beta is None:
        beta = os.environ.get("PYXI_BETA") == "1"
    # The Pyxi server said why it won't answer for this account (the trial or the plan ended,
    # a payment failed, the allowance is used, the sign-in no longer holds): its own sentence,
    # which has the date in it. First, because the model client wraps it as a 402 or a 429.
    gate = account.gate_of(exc, account_device=account.signed_in_here())
    if gate is not None:
        return Plain(account.line_for(*gate), "plan")
    if "invalid_grant" in low or names & {"RefreshError", "GoogleExpired"}:
        return Plain(GOOGLE_EXPIRED, "google")
    if GOOGLE_OUR_SIDE in text:
        return Plain(GOOGLE_OUR_SIDE, "google")  # an API of ours is off: nothing for them to do
    if "switched off in Pyxi's Google Cloud project" in text:
        # Only someone with a Google client of their own gets this (connectors/google.py):
        # it says what to turn on, with the link.
        return Plain(text.strip()[:300], "google")
    if "Google said " in text and (line := _google(text, low)):
        return Plain(line, "google")
    said = re.search(r"\b(LinkedIn|Discord) said (\d{3})", text)
    if said and (line := _service(said.group(1), int(said.group(2)))):
        return Plain(line, "account")
    is_model = bool(names & set(_MODEL_TYPES)) or bool(
        re.search(r"API error:|Error code: \d{3}|openrouter|anthropic", text)
    ) or bool(_BETA_SAID.search(text))
    if is_model and (line := _model(text, low, names, beta)):
        return Plain(line, "model")
    # A teammate's model error, however it was worded. (Only for jobs: in a turn a bare
    # "401" may be some other service's, and is said as that service's refusal.)
    if job and not re.search(r"\b\w+ said \d{3}", text):
        if re.search(r"\b401\b|authentication|invalid (x-)?api.?key", low):
            return Plain(MODEL_KEY_BETA if beta else MODEL_KEY, "model")
        if re.search(r"\b429\b|rate.?limit|overloaded", low):
            return Plain(MODEL_BUSY, "model")
    if "BudgetExceededError" in names or re.search(
        r"budget[ _](exceeded|exhausted|reached)|cost limit|max_cost", low
    ):
        return Plain(BUDGET_JOB, "model")
    if "browsertype.launch" in low or "executable doesn't exist" in low:
        return Plain(BROWSER_START, "browser")
    if "TargetClosedError" in names or re.search(
        r"target (page, context or browser|closed)|(browser|page|context) has been closed", low
    ):
        return Plain(BROWSER_CLOSED, "browser")
    if "net::err_name_not_resolved" in low or "net::err_internet" in low:
        return Plain(SITE_UNREACHABLE, "net")
    if "net::err" in low:
        return Plain(SITE_FAILED, "net")
    if names & set(_NET_TYPES) or _NO_NET.search(text):
        return Plain(OFFLINE, "net")
    if "com_error" in names or _COM_REFUSAL.search(text):
        missing = re.search(r"-2147221005|-2147221164|invalid class string|class not registered", low)
        return Plain(OFFICE_MISSING if missing else OFFICE_REFUSED, "office")
    about_a_file = (
        isinstance(exc, OSError)
        or bool(names & _FILE_TYPES)
        or bool(
            re.search(
                r"\[errno \d+\]|\[winerror \d+\]|file is not a zip file|package not found at|"
                r"cannot identify image file|eof marker not found|file has not been decrypted",
                low,
            )
        )
    )
    if about_a_file and (line := _file(exc, text, low, names)):
        return Plain(line, "file")
    if names & set(_TIMEOUT_TYPES) or "timeout" in low or "timed out" in low:
        if "Google" in text:
            return Plain(GOOGLE_SLOW, "net")
        if "timeout" in low and not names & {"ReadTimeout", "WriteTimeout", "PoolTimeout"}:
            return Plain(SITE_SLOW, "net")  # a page that never loaded (the browser's own words)
        return Plain(SLOW, "net")
    return None


# ------------------------------------------------------------------ the last resort
# Anything of Python's, or of a library's insides, that must never be read by a person.
_NOT_FOR_PEOPLE = re.compile(
    # Python's own words, as Python spells them...
    r"Traceback|\b\w*(?:Error|Exception|Warning)\b|\[Errno|\[WinError|\berrno\b|"
    r"0x[0-9a-fA-F]{4,}|object has no attribute|NoneType|, line \d+|\bself\.|__\w+__|"
    r"Error code:|status_code|<[\w.]+ (?:object|at) |<class |\\n|\\\\|"
    r"\bdict\b|\btuple\b|\bstr\b|\bint\b|\bNone\b|\bNaN\b|\bnull\b|"
    # ...a tuple, a dict or a list written out...
    r"\{\s*['\"]|['\"]\s*:\s*['\"{\[]|\(\s*['\"-]|\[\s*['\"{]|"
    # ...an address that carries a key, and keys themselves...
    r"(?i:https?://\S*[?&][\w-]*(?:token|key|code|sig|secret|auth)[\w-]*=)|"
    r"\b(?:sk|pk|rk)-[\w-]{8,}|\bpyxi_[\w-]{8,}|\bAIza[\w-]{10,}|\bya29\.[\w.-]+|"
    r"\bgh[pousr]_\w{10,}|\bxox[abprs]-[\w-]{8,}|\beyJ[\w-]{10,}\.|"
    r"(?<![\w/.-])[A-Za-z0-9_+/=-]{32,}(?![\w/.-])|"
    # ...and the words of the plumbing.
    r"(?i:invalid_grant|oauth|\bscopes?\b|\b(?:access|refresh|bearer|id)[ _]tokens?\b|\bjson\b|"
    r"\bhttp \d{3}\b|\bstack trace\b)"
)
_PATH = re.compile(r"(?i)\b[a-z]:[\\/]|[\\/](?:users|home|appdata)[\\/]|\\\\[\w.-]+\\")
_QUOTED_PATH = re.compile(r"(?i)['\"]((?:[a-z]:|\\\\)?[^'\"\n]*[\\/])([^'\"\\/\n]+)['\"]")


def safe_line(text: str, fallback: str = UNKNOWN, limit: int = 160) -> str:
    """The first line of ``text`` if a person could be shown it, else ``fallback``. A sentence
    of Pyxi's own ("I can't find a tab like that.") passes; "KeyError: 'id'", a tuple, a dict,
    a path with the Windows user name, a web address with a key in it, and a key do not."""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return fallback
    line = _QUOTED_PATH.sub(lambda m: m.group(2), lines[0])  # 'C:\Users\x\a.pdf' -> a.pdf
    if _PATH.search(line) or _NOT_FOR_PEOPLE.search(line):
        return fallback
    if not re.match(r"[A-Z0-9]", line) or " " not in line or line[0] in "([{<'\"":
        return fallback  # not a sentence: a bare code, a repr, a lone word
    if "[hidden]" in scrub(line):  # something that looks like a key or a password
        return fallback
    if len(line) > limit:
        head = line[:limit]
        cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
        line = head[: cut + 1] if cut >= limit // 3 else head[: head.rfind(" ")].rstrip(",;:") + "…"
    return line


def plain_failure(
    exc: BaseException, fallback: str = UNKNOWN, limit: int = 160, *, job: bool = False
) -> str:
    """One line for any failure: a known one's own words, else what is safe to show."""
    known = explain(exc, job=job)
    return known.line if known else safe_line(str(exc), fallback, limit)
