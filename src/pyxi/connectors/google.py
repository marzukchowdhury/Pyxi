"""Google: Gmail, Calendar, Docs and Sheets with one sign-in.

Setup (once): create a free Google Cloud project, enable the Gmail, Calendar, Docs, Sheets and
Drive APIs,
make an OAuth client of type "Desktop app", and save its JSON as ``.pyxi/google_client.json``
(or set ``GOOGLE_CLIENT_ID`` and ``GOOGLE_CLIENT_SECRET``). Then ``pyxi connect google`` (or the
Connect button in Settings) opens Google's sign-in in your browser. Pyxi never sees your
password; Google hands back a token that stays in ``.pyxi/connectors/google.json``.
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import mimetypes
import os
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from pyxi import secretstore
from pyxi.connectors import Connector, Outgoing, Result, Status, Tool, tool
from pyxi.timecontext import zone

EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _clock(t: datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
    # Ledger edits Google Sheets, and finds a Sheet or Doc by its name ("my sales sheet").
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary"
DOCS = "https://docs.googleapis.com/v1/documents"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3/files"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def google_error(status: int, body: str) -> str:
    """Google's refusal in words someone can act on. An API switched off in the Google Cloud
    project (Sheets was added after the first setup guide) says which one and where to turn it
    on, instead of 'Google said 403: {'."""
    try:
        err = json.loads(body).get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    message = str(err.get("message") or body).strip()
    reasons = {str(d.get("reason")) for d in err.get("details") or [] if isinstance(d, dict)}
    if "SERVICE_DISABLED" in reasons or "has not been used in project" in message:
        api = re.search(r"^(.+? API) has not been used", message)
        service = re.search(r"\b([a-z]+\.googleapis\.com)\b", message)
        project = re.search(r"project (\d+)", message)
        name = api.group(1) if api else "A Google API Pyxi needs"
        link = (
            f"https://console.cloud.google.com/apis/library/{service.group(1)}"
            + (f"?project={project.group(1)}" if project else "")
            if service
            else "https://console.cloud.google.com/apis/library"
        )
        return (
            f"The {name} is switched off in Pyxi's Google Cloud project. Turn it on at {link} "
            "(press Enable), wait a minute, then ask again."
        )
    return f"Google said {status}: {message[:200]}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]  # fmt: skip
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUMBERS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "few": 3}


def date_range(phrase: str, now: datetime) -> tuple[datetime, datetime] | None:
    """Everyday time words ("last week", "this month", "yesterday", "in October", "the past 3
    days") as a start and end in the user's own time zone, so Gmail gets exact bounds instead of
    the model guessing dates. None when the phrase isn't one of these."""
    p = re.sub(r"[^a-z0-9 ]", " ", phrase.lower()).strip()
    if p.startswith("since "):  # "since October": from then until now
        span = date_range(p[6:], now)
        return (
            (span[0], now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
            if span
            else None
        )
    p = re.sub(r"\b(the|in|on|during|from|of|over)\b", " ", p)
    p = re.sub(r"\s+", " ", p).strip()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())  # this Monday
    month = day.replace(day=1)
    end = day + timedelta(days=1)  # through the end of today

    def month_start(year: int, m: int) -> datetime:
        return day.replace(year=year + (m - 1) // 12, month=(m - 1) % 12 + 1, day=1)

    if p == "today":
        return day, end
    if p == "yesterday":
        return day - timedelta(days=1), day
    if p in ("this week", "week"):
        return week, end
    if p == "last week":
        return week - timedelta(days=7), week
    if p in ("this month", "month"):
        return month, end
    if p == "last month":
        return month_start(day.year, day.month - 1), month
    if p in ("this year", "year"):
        return day.replace(month=1, day=1), end
    if p == "last year":
        start = day.replace(year=day.year - 1, month=1, day=1)
        return start, start.replace(year=day.year)
    if p in ("recent", "recently", "lately"):
        return day - timedelta(days=30), end
    m = re.fullmatch(r"(?:last|past|previous) (\d+|[a-z]+)? ?(day|week|month)s?", p)
    if m:
        n = int(m.group(1)) if (m.group(1) or "").isdigit() else NUMBERS.get(m.group(1) or "a")
        if n:
            days = {"day": 1, "week": 7, "month": 30}[m.group(2)] * n
            return day - timedelta(days=days - 1 if m.group(2) == "day" else days), end
    m = re.fullmatch(r"(?:last )?(" + "|".join(WEEKDAYS) + ")", p)
    if m:  # the most recent such day ("Monday" on a Monday means today)
        back = (day.weekday() - WEEKDAYS.index(m.group(1))) % 7
        if p.startswith("last ") and back == 0:
            back = 7
        start = day - timedelta(days=back)
        return start, start + timedelta(days=1)
    m = re.fullmatch(r"(?:last )?(" + "|".join(MONTHS) + r")(?: (\d{4}))?", p)
    if m:  # the most recent such month, or that month of the year they said
        n = MONTHS.index(m.group(1)) + 1
        year = int(m.group(2)) if m.group(2) else (day.year if n <= day.month else day.year - 1)
        start = month_start(year, n)
        return start, min(month_start(year, n + 1), end)
    return None


def _term(field: str, value: str) -> str:
    value = value.strip().strip('"')
    return f'{field}:"{value}"' if " " in value else f"{field}:{value}"


def build_query(args: dict[str, Any], now: datetime) -> tuple[str, str]:
    """A Gmail query from plain fields, plus a note on anything it couldn't use. A raw ``query``
    still works and is kept as is; the fields are added to it."""
    parts = [str(args.get("query") or "").strip()]
    note = ""
    for field in ("from", "to"):
        for who in re.split(r"\s*(?:,|\bor\b)\s*", str(args.get(field) or "")):
            if who.strip():
                parts.append(_term(field, who))
    if args.get("sent"):
        parts.append("in:sent")
    if args.get("unread"):
        parts.append("is:unread")
    if args.get("has_attachment") or args.get("filename"):
        parts.append("has:attachment")
    if args.get("filename"):
        name = str(args["filename"]).strip().lstrip("*.")
        parts.append(_term("filename", name))
    if args.get("words"):
        parts.append(str(args["words"]).strip())
    when = str(args.get("when") or "").strip()
    after, before = str(args.get("after") or "").strip(), str(args.get("before") or "").strip()
    span = date_range(when, now) if when else None
    if when and span is None:
        note = f" (I couldn't read the time {when!r}, so I searched all dates)"
    if span:
        parts += [f"after:{int(span[0].timestamp())}", f"before:{int(span[1].timestamp())}"]
    for field, value in (("after", after), ("before", before)):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parts.append(f"{field}:{value.replace('-', '/')}")
    return " ".join(p for p in parts if p), note


NOREPLY = re.compile(r"no[-_.]?reply|do[-_.]?not[-_.]?reply|notifications?@|mailer-daemon", re.I)
FILLER = {
    "my",
    "the",
    "a",
    "an",
    "to",
    "from",
    "email",
    "mail",
    "reply",
    "back",
    "at",
    "our",
    "me",
    "friend",
    "buddy",
    "pal",
    "mate",
    "colleague",
    "coworker",
    "mr",
    "mrs",
    "ms",
    "dr",
    "miss",
    "his",
    "her",
    "their",
    "that",
    "guy",
}


ROLES = {
    w: q
    for words, q in (
        (
            ("professor", "prof", "lecturer", "instructor"),
            "{professor prof dr lecturer instructor}",
        ),
        (("teacher", "tutor"), "{teacher tutor class}"),
        (("boss", "manager", "supervisor"), "{manager boss supervisor}"),
        (("advisor", "adviser"), "{advisor adviser}"),
        (("landlord", "landlady", "property"), "{landlord rent lease tenancy}"),
        (("doctor", "gp", "dentist", "clinic"), "{doctor dr clinic appointment dental}"),
        (("coach",), "{coach practice}"),
        (("mom", "mum", "mother"), "{mom mum mother}"),
        (("dad", "father"), "{dad father}"),
        (("recruiter", "hr"), "{recruiter interview application}"),
    )
    for w in words
}


def _matches(word: str, name: str, address: str) -> bool:
    """ "sam" matches Sam Rahman and sam.r@uni.edu, not Samantha's colleague "Osama"."""
    if "@" in word:
        return word == address.lower()
    parts = re.findall(r"[a-z0-9]+", f"{name} {address.split('@')[0]}".lower())
    return any(p.startswith(word) for p in parts)


def _when(meta: dict[str, Any], headers: dict[str, str]) -> datetime | None:
    if meta.get("internalDate"):
        return datetime.fromtimestamp(int(meta["internalDate"]) / 1000).astimezone()
    try:
        when = parsedate_to_datetime(headers.get("date", ""))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _person(address: str, p: dict[str, Any]) -> str:
    bits = []
    if p["wrote"]:
        bits.append(f"you wrote to them {p['wrote']}x")
    if p["heard"]:
        bits.append(f"they wrote {p['heard']}x")
    if p["last"]:
        bits.append(f"last {p['last']:%d %b %Y}")
    if NOREPLY.search(address):
        bits.append("a no-reply address")
    return f"- {_who(p['name'], address)}: {', '.join(bits)}"


def _first_line(body: str) -> str:
    """A one-line gist when the model didn't give one: the first real sentence of the email."""
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    lines = [
        ln
        for ln in lines
        if not re.fullmatch(r"(hi|hey|hello|dear)([ ,][^.?]{0,40})?[,!]?", ln, re.I)
    ]
    text = " ".join(lines) or body
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text)[:160]


def _who(name: str, address: str) -> str:
    """How the approval card and summaries show someone: "Sam Rahman (sam@example.com)"."""
    name = name.strip().strip('"')
    return f"{name} ({address})" if name and name.lower() != address.lower() else address


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:120] or "attachment"


def event_time(when: datetime, tz_name: str | None) -> dict[str, str]:
    """A start or end time Google Calendar accepts. With the calendar's own IANA zone
    ("America/Vancouver") it's local time plus that zone. Without it, the exact offset goes in the
    time itself: Windows' zone names ("Pacific Standard Time") aren't valid Google time zones, and
    sending one fails with "Invalid time zone definition"."""
    if tz_name and _is_iana(tz_name):
        return {
            "dateTime": when.replace(tzinfo=None).isoformat(timespec="seconds"),
            "timeZone": tz_name,
        }
    return {"dateTime": when.isoformat(timespec="seconds")}


def _is_iana(name: str) -> bool:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return "/" in name or name == "UTC"


# The installed app's private Python lives in resources/python; the build puts Pyxi's own Google
# OAuth client (a "Desktop app" client, which Google doesn't treat as secret) beside it.
BUNDLED_CLIENT = "google_client.json"


def bundled_client() -> Path | None:
    path = Path(sys.prefix) / BUNDLED_CLIENT
    return path if path.is_file() else None


class GoogleConnector(Connector):
    id = "google"
    name = "Google (Gmail, Calendar, Docs, Sheets)"

    def __init__(self, store: Path, downloads: Path) -> None:
        self.token_path = store / "google.json"
        self.client_path = store.parent / "google_client.json"
        self.downloads = downloads
        self._creds = None
        self.email: str | None = None
        self._tz: str | None = None
        self._names: dict[str, str] = {}  # address -> display name, from mail seen this session

    # ------------------------------------------------------------------ auth
    def client_config(self) -> dict[str, Any] | None:
        """Their own client (.pyxi/google_client.json or GOOGLE_CLIENT_ID/SECRET) wins; otherwise
        the one the installer ships for beta testers (desktop/build-secrets at build time), so a
        tester only presses Connect."""
        if self.client_path.exists():
            return json.loads(self.client_path.read_text(encoding="utf-8"))
        cid, secret = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
        if cid and secret:
            return {
                "installed": {
                    "client_id": cid,
                    "client_secret": secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        shipped = bundled_client()
        if shipped is not None:
            return json.loads(shipped.read_text(encoding="utf-8"))
        return None

    def _saved(self) -> dict[str, Any] | None:
        """The saved token (encrypted with DPAPI; an old plain file migrates on first read).
        None when there's none, or it was unreadable: then they simply connect again."""
        return secretstore.read_json(self.token_path)

    def connected(self) -> bool:
        return self.token_path.exists() and self._saved() is not None

    def status(self) -> Status:
        if self.connected():
            detail = self._saved_email() or "Connected"
            if self.missing_scopes():
                detail += ". Press Allow Sheets so Pyxi can work in Google Sheets too"
                return Status(self.id, self.name, True, detail, needs="Allow Sheets")
            return Status(self.id, self.name, True, detail)
        setup = [
            "Open console.cloud.google.com and create a project (free).",
            "Enable the Gmail, Google Calendar, Google Docs, Google Sheets and Google Drive APIs.",
            "OAuth consent screen: External, add yourself as a test user.",
            "Credentials > Create OAuth client > Desktop app > Download JSON.",
            "Save it as pyxi/.pyxi/google_client.json, then press Connect.",
        ]
        detail = (
            "Ready to sign in"
            if self.client_config()
            else "Needs a Google OAuth client (5 minutes, free)"
        )
        return Status(self.id, self.name, False, detail, setup)

    def _saved_email(self) -> str | None:
        return (self._saved() or {}).get("pyxi_email")

    def granted_scopes(self) -> set[str]:
        scopes = (self._saved() or {}).get("scopes") or []
        return set(scopes.split() if isinstance(scopes, str) else scopes)

    def missing_scopes(self) -> list[str]:
        """Scopes added since they connected (Sheets came later). Connect again to grant them."""
        granted = self.granted_scopes()
        if not granted:
            return []  # an old token that didn't record its scopes: let Google decide
        return [s for s in SCOPES if s not in granted and s != "openid"]

    def sign_in(self) -> str:
        """Run Google's sign-in in the browser (blocking; call in a thread)."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        config = self.client_config()
        if config is None:
            raise RuntimeError("No Google OAuth client yet: save it as .pyxi/google_client.json.")
        flow = InstalledAppFlow.from_client_config(config, SCOPES)
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            prompt="consent",
            success_message="Pyxi is connected to Google. You can close this tab.",
        )
        email = None
        try:
            r = httpx.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {creds.token}"},
                timeout=15,
            )
            email = r.json().get("email")
        except Exception:
            pass
        data = json.loads(creds.to_json())
        data["pyxi_email"] = email
        # What Google really granted: on its consent page each permission can be unticked, and
        # the requested list would pretend Sheets is allowed until the first Sheets job fails.
        granted = getattr(creds, "granted_scopes", None)
        if granted:
            data["scopes"] = sorted(granted.split() if isinstance(granted, str) else granted)
        secretstore.write_json(self.token_path, data)
        self._creds = None
        if SHEETS_SCOPE in self.missing_scopes():
            raise RuntimeError(
                "Google didn't give the Sheets permission. Press Allow Sheets and tick the "
                "Google Sheets box when Google asks"
            )
        return email or "your Google account"

    def disconnect(self) -> None:
        self.token_path.unlink(missing_ok=True)
        self._creds = None

    async def _token(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if self._creds is None:
            # The scopes they actually granted: asking a refresh for new ones (Sheets, added
            # later) would fail and take Gmail down with it until they reconnect.
            saved = self._saved()
            if saved is None:
                raise RuntimeError("Google isn't connected (or its sign-in expired): press Connect.")
            self._creds = Credentials.from_authorized_user_info(
                saved, sorted(self.granted_scopes()) or SCOPES
            )
        if not self._creds.valid:
            await asyncio.to_thread(self._creds.refresh, Request())
            data = json.loads(self._creds.to_json())
            data["pyxi_email"] = self._saved_email()
            secretstore.write_json(self.token_path, data)
        return self._creds.token

    async def _api(self, method: str, url: str, **kw: Any) -> Any:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(method, url, headers=headers, **kw)
        if r.status_code >= 400:
            raise RuntimeError(google_error(r.status_code, r.text))
        return r.json() if r.content else {}

    # ------------------------------------------------------------------ gmail
    async def _now(self) -> datetime:
        return datetime.now(zone(await self.time_zone()))

    async def _list(self, q: str, n: int) -> list[str]:
        listing = await self._api("GET", f"{GMAIL}/messages", params={"q": q, "maxResults": n})
        return [m["id"] for m in listing.get("messages", [])]

    async def gmail_search(self, args: dict[str, Any]) -> Result:
        """Search with plain fields (from, when, has_attachment...) or raw Gmail syntax. When
        nothing matches, it loosens the search step by step (the extra words, then the dates)
        and says so, rather than coming back empty for "the letter Nabiha sent last week"."""
        n = min(int(args.get("max", 6) or 6), 10)
        now = await self._now()
        q, note = build_query(args, now)
        if not q:
            return "gmail_search: say what to look for (from, words, when...)."
        ids = await self._list(q, n)
        looser = dict(args)
        for drop, why in (("words", "without the extra words"), ("when", "from any date")):
            if ids or not looser.get(drop) or not (set(looser) - {drop, "max"}):
                continue
            looser.pop(drop)
            if drop == "when":
                looser.pop("after", None), looser.pop("before", None)
            wider, _ = build_query(looser, now)
            if wider and wider != q:
                ids = await self._list(wider, n)
                if ids:
                    note += f" (nothing matched {q!r} exactly; these are {why}: {wider!r})"
        if not ids:
            return f"gmail_search: nothing matched {q!r}{note}."
        metas = await asyncio.gather(
            *(self._api("GET", f"{GMAIL}/messages/{i}", params={"format": "full"}) for i in ids)
        )
        return f"Emails (newest first){note}:\n" + "\n".join(self._summary(m) for m in metas)

    def _remember(self, *headers: str) -> None:
        for name, addr in getaddresses([h for h in headers if h]):
            if name and addr and "@" in addr:
                self._names.setdefault(addr.lower(), name.strip().strip('"'))

    async def gmail_people(self, args: dict[str, Any]) -> Result:
        """Who "Sam" or "my professor" is, from the people they really write to and hear from.
        Sent mail counts most (someone you write to is who you mean), recent mail breaks ties."""
        asked = str(args.get("name", "")).strip()
        words = [w for w in re.findall(r"[\w.'@+-]+", asked.lower()) if w not in FILLER]
        if not words:
            return "gmail_people: who are you looking for?"
        role = next((ROLES[w] for w in words if w in ROLES), None)
        named = role is None
        me = {(self.email or self._saved_email() or "").lower()} - {""}
        if named:  # a name or an address: match the headers
            probe = " ".join(words)
            queries = [(f"in:sent {_term('to', probe)}", True), (_term("from", probe), False)]
        else:  # a role ("my professor", "the landlord"): those words in the mail itself
            queries = [(f"in:sent {role}", True), (role, False)]
        found = await asyncio.gather(*(self._list(q, 15) for q, _ in queries))
        seen: dict[str, bool] = {}
        for (_, sent), ids in zip(queries, found, strict=True):
            for i in ids:
                seen[i] = seen.get(i, False) or sent
        metas = await asyncio.gather(
            *(
                self._api(
                    "GET",
                    f"{GMAIL}/messages/{i}",
                    params={"format": "metadata", "metadataHeaders": ["From", "To", "Cc", "Date"]},
                )
                for i in seen
            )
        )
        people: dict[str, dict[str, Any]] = {}
        for meta in metas:
            h = self._headers(meta)
            self._remember(h.get("from", ""), h.get("to", ""), h.get("cc", ""))
            when = _when(meta, h)
            outgoing = seen.get(meta.get("id", ""), False) or (
                parseaddr(h.get("from", ""))[1].lower() in me
            )
            field = ("to", "cc") if outgoing else ("from",)
            for name, addr in getaddresses([h.get(f, "") for f in field]):
                addr = addr.lower()
                if "@" not in addr or addr in me:
                    continue
                if named and not all(_matches(w, name, addr) for w in words):
                    continue
                p = people.setdefault(addr, {"name": "", "wrote": 0, "heard": 0, "last": None})
                p["name"] = p["name"] or name.strip().strip('"')
                p["wrote" if outgoing else "heard"] += 1
                if when and (p["last"] is None or when > p["last"]):
                    p["last"] = when
        if not people:
            return (
                f"gmail_people: no one like {asked!r} in their recent mail. Ask the user for "
                "the address (one short question)."
            )

        def score(item: tuple[str, dict[str, Any]]) -> float:
            addr, p = item
            return (3 * p["wrote"] + p["heard"]) * (0.2 if NOREPLY.search(addr) else 1)

        ranked = sorted(people.items(), key=score, reverse=True)
        lines = [_person(a, p) for a, p in ranked[:5]]
        top = score(ranked[0])
        rest = score(ranked[1]) if len(ranked) > 1 else 0.0
        # A clear match: the only one, or someone they write to far more than anyone else.
        clear = len(ranked) == 1 or (ranked[0][1]["wrote"] > 0 and top >= 3 * max(rest, 1))
        if clear:
            addr, p = ranked[0]
            return (
                f"Clear match for {asked!r}: {_who(p['name'], addr)}. Use this address.\n"
                + "\n".join(lines)
            )
        return (
            f"Several people could be {asked!r}. Ask the user which one, in one short question "
            "naming the top two or three:\n" + "\n".join(lines)
        )

    def _headers(self, msg: dict[str, Any]) -> dict[str, str]:
        return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

    def _attachments(self, part: dict[str, Any], out: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if part.get("filename") and part.get("body", {}).get("attachmentId"):
            out.append(
                {
                    "filename": part["filename"],
                    "id": part["body"]["attachmentId"],
                    "size": part["body"].get("size", 0),
                    "mime": part.get("mimeType"),
                }
            )
        for p in part.get("parts", []) or []:
            self._attachments(p, out)
        return out

    def _summary(self, msg: dict[str, Any]) -> str:
        h = self._headers(msg)
        self._remember(h.get("from", ""), h.get("to", ""))
        atts = self._attachments(msg.get("payload", {}), [])
        files = f" | attachments: {', '.join(a['filename'] for a in atts)}" if atts else ""
        return f"- id={msg['id']} from {h.get('from', '?')} | {h.get('subject', '(no subject)')} | {h.get('date', '')}{files}\n  {msg.get('snippet', '')[:160]}"

    def _body(self, part: dict[str, Any]) -> str:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            return _unb64url(data).decode("utf-8", "replace")
        texts = [self._body(p) for p in part.get("parts", []) or []]
        plain = next((t for t in texts if t.strip()), "")
        if plain:
            return plain
        if data and mime == "text/html":
            return _strip_html(_unb64url(data).decode("utf-8", "replace"))
        return ""

    def _html(self, part: dict[str, Any]) -> str:
        data = part.get("body", {}).get("data")
        if data and part.get("mimeType") == "text/html":
            return _unb64url(data).decode("utf-8", "replace")
        return "".join(self._html(p) for p in part.get("parts", []) or [])

    async def gmail_fetch(self, query: str, n: int = 40) -> list[dict[str, Any]]:
        """Whole emails for Pyxi's own background checks (receipts, orders): sender, subject,
        date, plain text and every link with its words. Fetched ten at a time, so a big scan
        stays under Gmail's rate limit."""
        ids = await self._list(query, min(n, 100))
        msgs: list[dict[str, Any]] = []
        for i in range(0, len(ids), 10):
            msgs += await asyncio.gather(
                *(
                    self._api("GET", f"{GMAIL}/messages/{m}", params={"format": "full"})
                    for m in ids[i : i + 10]
                )
            )
        out = []
        for msg in msgs:
            h = self._headers(msg)
            payload = msg.get("payload", {})
            raw = self._html(payload)
            links = [
                (_strip_html(text).strip(), html.unescape(url))
                for url, text in re.findall(
                    r"""<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""", raw, re.S | re.I
                )
            ]
            text = self._body(payload)
            for m in re.finditer(r"([^\n]{0,60}?)\s*(https?://[^\s<>\"')]+)", text):
                links.append((m.group(1).strip(" :-"), m.group(2)))
            out.append(
                {
                    "id": msg.get("id", ""),
                    "from": h.get("from", ""),
                    "subject": h.get("subject", ""),
                    "date": _when(msg, h),
                    "text": text or _strip_html(raw),
                    "links": links,
                }
            )
        return out

    async def gmail_read(self, args: dict[str, Any]) -> Result:
        msg = await self._api("GET", f"{GMAIL}/messages/{args['id']}", params={"format": "full"})
        h = self._headers(msg)
        self._remember(h.get("from", ""), h.get("to", ""), h.get("cc", ""))
        atts = self._attachments(msg.get("payload", {}), [])
        body = re.sub(r"\n{3,}", "\n\n", self._body(msg.get("payload", {}))).strip()[:5000]
        files = "\n".join(f"- {a['filename']} ({a['size'] // 1024} KB)" for a in atts) or "none"
        return (
            f"Email id={msg['id']} thread={msg.get('threadId')}\nFrom: {h.get('from')}\nTo: {h.get('to')}\n"
            f"Subject: {h.get('subject')}\nDate: {h.get('date')}\nAttachments:\n{files}\n\n{body}"
        )

    async def gmail_download(self, args: dict[str, Any]) -> Result:
        msg = await self._api("GET", f"{GMAIL}/messages/{args['id']}", params={"format": "full"})
        atts = self._attachments(msg.get("payload", {}), [])
        want = str(args.get("filename") or "").lower()
        chosen = [a for a in atts if not want or want in a["filename"].lower()] or atts
        if not chosen:
            return "gmail_download: that email has no attachments."
        self.downloads.mkdir(parents=True, exist_ok=True)
        saved = []
        for a in chosen[:5]:
            data = await self._api("GET", f"{GMAIL}/messages/{args['id']}/attachments/{a['id']}")
            path = self.downloads / _safe_name(a["filename"])
            path.write_bytes(_unb64url(data["data"]))
            saved.append(str(path))
        return "Saved to this PC:\n" + "\n".join(f"- {p}" for p in saved)

    async def gmail_send(self, args: dict[str, Any]) -> Result:
        """Prepare an email (new or a reply, optionally with files) and ask before sending. The
        approval card reads back exactly what Pyxi understood: who (name and address), the
        subject, and the gist in one line, then the email itself."""
        to = str(args.get("to", "")).strip()
        subject = str(args.get("subject", "")).strip()
        body = str(args.get("body", "")).strip()
        files = [Path(p) for p in args.get("attachments", []) or []]
        missing = [str(p) for p in files if not p.is_file()]
        if missing:
            return f"gmail_send: can't find {missing}."
        if not body:
            return "gmail_send: what should the email say?"
        thread_id = None
        replying = ""
        msg = EmailMessage()
        if args.get("reply_to_id"):
            orig = await self._api(
                "GET", f"{GMAIL}/messages/{args['reply_to_id']}", params={"format": "metadata"}
            )
            h = self._headers(orig)
            thread_id = orig.get("threadId")
            sender = h.get("reply-to") or h.get("from", "")
            self._remember(h.get("from", ""), sender)
            to = to or sender
            subject = subject or (
                "Re: " + re.sub(r"^(re:\s*)+", "", h.get("subject", ""), flags=re.I)
            )
            replying = f"\nReplying to: {h.get('subject') or '(no subject)'}" + (
                f" ({h['date'][:16].strip()})" if h.get("date") else ""
            )
            if h.get("message-id"):
                msg["In-Reply-To"] = h["message-id"]
                msg["References"] = (h.get("references", "") + " " + h["message-id"]).strip()
        people = [(n, a.strip()) for n, a in getaddresses([to]) if a.strip()]
        bad = [a for _, a in people if not EMAIL.fullmatch(a)]
        if not people:
            return "gmail_send: who should it go to? Find them with gmail_people first."
        if bad:
            return f"gmail_send: {bad} aren't email addresses. Find them with gmail_people first."
        if not subject:
            return "gmail_send: give it a short subject."
        names = [(n.strip().strip('"') or self._names.get(a.lower(), ""), a) for n, a in people]
        shown = ", ".join(_who(n, a) for n, a in names)
        msg["To"] = ", ".join(f'"{n}" <{a}>' if n else a for n, a in names)
        msg["Subject"] = subject
        msg.set_content(body)
        for p in files:
            ctype, _ = mimetypes.guess_type(p.name)
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
        raw = {"raw": _b64url(msg.as_bytes()), **({"threadId": thread_id} if thread_id else {})}

        async def send() -> str:
            # Sent only on approval; a declined email leaves nothing behind (no stray draft).
            await self._api("POST", f"{GMAIL}/messages/send", json=raw)
            return f'Sent to {shown}: "{subject}".'

        gist = re.sub(r"\s+", " ", str(args.get("gist") or "")).strip() or _first_line(body)
        attached = f"\nAttached: {', '.join(f'{p.name} (from {p.parent})' for p in files)}" if files else ""
        # The card shows the whole email exactly as it's sent: every address, the subject, and
        # the full body (the card scrolls). The Gist line is Pyxi's own summary, not sent.
        return Outgoing(
            action=f"Send an email to {shown}",
            preview=f"To: {shown}\nSubject: {subject}\nGist: {gist[:160]}{replying}{attached}\n\n{body}",
            run=send,
            recipients=[a for _, a in names],
            payload=body,
            channel="email",
        )

    # ------------------------------------------------------------------ calendar
    async def calendar_list(self, args: dict[str, Any]) -> Result:
        days = float(args.get("days", 7) or 7)
        now = datetime.now(zone(await self.time_zone()))
        params = {
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(days=days)).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 15,
        }
        if args.get("query"):
            params["q"] = str(args["query"])
        events = (await self._api("GET", f"{CAL}/events", params=params)).get("items", [])
        if not events:
            return f"Calendar: nothing in the next {days:g} days."
        lines = []
        for e in events:
            start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")
            who = ", ".join(a.get("email", "") for a in e.get("attendees", [])[:4])
            lines.append(
                f"- {start} {e.get('summary', '(untitled)')}"
                + (f" with {who}" if who else "")
                + (f" @ {e['location']}" if e.get("location") else "")
            )
        return "Upcoming:\n" + "\n".join(lines)

    async def time_zone(self) -> str | None:
        """The calendar's own time zone (what "3pm" means to the user)."""
        if self._tz is None:
            try:
                data = await self._api(
                    "GET", "https://www.googleapis.com/calendar/v3/users/me/settings/timezone"
                )
                self._tz = str(data.get("value") or "")
            except Exception as exc:
                log.warning("couldn't read the calendar's time zone: %s", exc)
                return None  # try again next time rather than remembering the failure
        return self._tz or None

    async def calendar_add(self, args: dict[str, Any]) -> Result:
        """Add an event, checked before and read back after, so mistakes are caught."""
        tz_name = await self.time_zone()
        tz = zone(tz_name)
        now = datetime.now(tz)
        try:
            start = datetime.fromisoformat(str(args["start"]))
        except (KeyError, ValueError):
            return "calendar_add: start must be a local date and time like 2026-11-06T15:00."
        start = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
        if args.get("end"):
            try:
                end = datetime.fromisoformat(str(args["end"]))
            except ValueError:
                return "calendar_add: end must look like 2026-11-06T15:30."
            end = end.replace(tzinfo=tz) if end.tzinfo is None else end.astimezone(tz)
        else:
            end = start + timedelta(minutes=int(args.get("minutes", 30) or 30))
        if start < now - timedelta(minutes=5):
            return (
                f"calendar_add: {start:%a %d %b %Y %H:%M} is in the past (it's now {now:%a %d %b %Y %H:%M}). "
                "Check the date before trying again."
            )
        if end <= start:
            return "calendar_add: the end must be after the start."
        raw_guests = [str(g).strip() for g in args.get("attendees", []) or []]
        bad = [g for g in raw_guests if not EMAIL.fullmatch(g)]
        if bad:
            return f"calendar_add: {bad} aren't valid email addresses. Use the exact address the user gave."
        guests = sorted(set(raw_guests), key=raw_guests.index)
        meet = bool(args.get("meet", bool(guests)))
        event: dict[str, Any] = {
            "summary": str(args.get("title") or "Meeting"),
            "start": event_time(start, tz_name),
            "end": event_time(end, tz_name),
        }
        if args.get("location"):
            event["location"] = str(args["location"])
        if args.get("description"):
            event["description"] = str(args["description"])
        if guests:
            event["attendees"] = [{"email": g} for g in guests]
        if meet:
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
        when = f"{start:%A} {start.day} {start:%B %Y}, {_clock(start)} to {_clock(end)}"

        async def create() -> str:
            made = await self._api(
                "POST",
                f"{CAL}/events",
                params={
                    "sendUpdates": "all" if guests else "none",
                    "conferenceDataVersion": 1 if meet else 0,
                },
                json=event,
            )
            # Read it back from Google, so the summary reports what's really on the calendar.
            got = await self._api("GET", f"{CAL}/events/{made['id']}") if made.get("id") else made
            s0 = datetime.fromisoformat(got["start"]["dateTime"]).astimezone(tz)
            e0 = datetime.fromisoformat(got["end"]["dateTime"]).astimezone(tz)
            invited = [a.get("email") for a in got.get("attendees", []) if not a.get("self")]
            meet_link = got.get("hangoutLink") or next(
                (
                    ep.get("uri")
                    for ep in (got.get("conferenceData") or {}).get("entryPoints", [])
                    if ep.get("entryPointType") == "video"
                ),
                "",
            )
            return (
                f"CREATED '{got.get('summary')}' | when: {s0:%A} {s0.day} {s0:%B %Y}, {_clock(s0)} to {_clock(e0)} ({tz_name or 'local time'})"
                + (f" | invited: {', '.join(invited)}" if invited else "")
                + (f" | meet: {meet_link}" if meet_link else "")
                + f" | link: {got.get('htmlLink', '')}"
            )

        if not guests:  # only your own calendar: nothing leaves, no approval needed
            return await create()
        # Everything Google sends the guests: who, what, when, where, and the description.
        details = [f"Guests: {', '.join(guests)}", f"Title: {event['summary']}", f"When: {when}"]
        if event.get("location"):
            details.append(f"Where: {event['location']}")
        if meet:
            details.append("With a Google Meet link")
        if event.get("description"):
            details.append(f"\n{event['description']}")
        return Outgoing(
            action=f"Invite {', '.join(guests)} to '{event['summary']}'",
            preview="\n".join(details),
            run=create,
            kind="invite",
            recipients=guests,
            payload=str(event.get("description") or ""),
            channel="invite",
        )

    # ------------------------------------------------------------------ docs
    async def docs_read(self, args: dict[str, Any]) -> Result:
        doc_id = self._doc_id(str(args["doc"]))
        doc = await self._api("GET", f"{DOCS}/{doc_id}")
        text = []
        for block in doc.get("body", {}).get("content", []):
            for el in block.get("paragraph", {}).get("elements", []):
                text.append(el.get("textRun", {}).get("content", ""))
        return f"Google Doc '{doc.get('title')}':\n" + "".join(text)[:8000]

    async def docs_create(self, args: dict[str, Any]) -> Result:
        doc = await self._api("POST", DOCS, json={"title": str(args.get("title", "Untitled"))})
        text = str(args.get("text", ""))
        if text:
            await self._api(
                "POST",
                f"{DOCS}/{doc['documentId']}:batchUpdate",
                json={"requests": [{"insertText": {"location": {"index": 1}, "text": text}}]},
            )
        return (
            f"Created the Google Doc: https://docs.google.com/document/d/{doc['documentId']}/edit"
        )

    # ------------------------------------------------------------------ sheets (Ledger)
    def _need_sheets(self) -> None:
        if SHEETS_SCOPE in self.missing_scopes():
            raise RuntimeError(
                "Pyxi needs one more OK from Google to work in Sheets: in Settings, press "
                "Allow Sheets next to Google."
            )

    async def sheets_meta(self, sheet_id: str) -> dict[str, Any]:
        self._need_sheets()
        return await self._api(
            "GET", f"{SHEETS}/{sheet_id}", params={"fields": "properties.title,sheets.properties"}
        )

    async def sheets_values(
        self, sheet_id: str, a1: str, *, formulas: bool = False
    ) -> list[list[Any]]:
        self._need_sheets()
        data = await self._api(
            "GET",
            f"{SHEETS}/{sheet_id}/values/{a1}",
            params={"valueRenderOption": "FORMULA" if formulas else "FORMATTED_VALUE"},
        )
        return data.get("values", [])

    async def sheets_write(self, sheet_id: str, data: list[dict[str, Any]]) -> Any:
        self._need_sheets()
        return await self._api(
            "POST",
            f"{SHEETS}/{sheet_id}/values:batchUpdate",
            json={"valueInputOption": "USER_ENTERED", "data": data},
        )

    async def sheets_append(self, sheet_id: str, a1: str, rows: list[list[Any]]) -> Any:
        self._need_sheets()
        return await self._api(
            "POST",
            f"{SHEETS}/{sheet_id}/values/{a1}:append",
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": rows},
        )

    async def sheets_batch(self, sheet_id: str, requests: list[dict[str, Any]]) -> Any:
        self._need_sheets()
        return await self._api(
            "POST", f"{SHEETS}/{sheet_id}:batchUpdate", json={"requests": requests}
        )

    async def docs_get(self, doc_id: str) -> dict[str, Any]:
        return await self._api("GET", f"{DOCS}/{doc_id}")

    async def docs_batch(self, doc_id: str, requests: list[dict[str, Any]]) -> Any:
        return await self._api("POST", f"{DOCS}/{doc_id}:batchUpdate", json={"requests": requests})

    async def drive_find(self, name: str, kind: str) -> list[dict[str, Any]]:
        """Sheets or Docs whose name contains these words, newest first."""
        mime = {
            "sheet": "application/vnd.google-apps.spreadsheet",
            "doc": "application/vnd.google-apps.document",
        }[kind]
        words = [w.replace("'", "\\'") for w in re.findall(r"[\w-]+", name)[:4]]
        q = " and ".join(
            [f"mimeType='{mime}'", "trashed=false", *(f"name contains '{w}'" for w in words)]
        )
        data = await self._api(
            "GET",
            DRIVE,
            params={
                "q": q,
                "orderBy": "modifiedTime desc",
                "pageSize": 5,
                "fields": "files(id,name)",
            },
        )
        return data.get("files", [])

    async def open_for_edit(self, ref: str) -> Any:
        """A Google Sheet or Doc for Ledger, from a link, an id, or words from its name."""
        from pyxi.agents.ledger import GoogleDoc, GoogleSheet, LedgerError

        ref = ref.strip()
        if m := re.search(r"/spreadsheets/d/([\w-]+)", ref):
            return GoogleSheet(self, m.group(1))
        if m := re.search(r"/document/d/([\w-]+)", ref):
            return GoogleDoc(self, m.group(1))
        if m := re.match(r"(?i)new (sheet|doc)\s*:\s*(.+)", ref):  # make one for them
            title = m.group(2).strip()[:120]
            if m.group(1).lower() == "sheet":
                self._need_sheets()
                made = await self._api("POST", SHEETS, json={"properties": {"title": title}})
                return GoogleSheet(self, made["spreadsheetId"])
            made = await self._api("POST", DOCS, json={"title": title})
            return GoogleDoc(self, made["documentId"])
        if re.fullmatch(r"[\w-]{25,}", ref):  # a bare id: ask Drive which kind it is
            meta = await self._api("GET", f"{DRIVE}/{ref}", params={"fields": "mimeType"})
            if "document" in meta.get("mimeType", ""):
                return GoogleDoc(self, ref)
            return GoogleSheet(self, ref)
        kind = "doc" if re.search(r"\bdoc(ument)?s?\b", ref, re.I) else "sheet"
        words = re.sub(
            r"(?i)\b(my|the|a|google|sheets?|spreadsheet|docs?|document|in|on|called|named)\b",
            " ",
            ref,
        ).strip()
        found = await self.drive_find(words or ref, kind)
        if not found:
            other = "sheet" if kind == "doc" else "doc"
            found = await self.drive_find(words or ref, other)
            kind = other if found else kind
        if not found:
            what = "Doc" if kind == "doc" else "Sheet"
            raise LedgerError(f"I couldn't find a Google {what} called {ref!r}.")
        f = found[0]
        return GoogleDoc(self, f["id"]) if kind == "doc" else GoogleSheet(self, f["id"])

    async def sheets_read(self, args: dict[str, Any]) -> Result:
        target = await self.open_for_edit(str(args["sheet"]))
        await target.load()
        return target.describe()[:8000]

    @staticmethod
    def _doc_id(ref: str) -> str:
        m = re.search(r"/document/d/([\w-]+)", ref)
        return m.group(1) if m else ref.strip()

    # ------------------------------------------------------------------ tools
    def tools(self) -> list[Tool]:
        return [
            Tool(
                tool(
                    "gmail_search",
                    "Search the user's Gmail. Prefer the plain fields; they are combined. 'The letter Nabiha "
                    "sent last week' is from='Nabiha', when='last week', has_attachment=true. 'Anything from "
                    "Amazon this month' is from='Amazon', when='this month'. If nothing matches it loosens "
                    "the search itself and says so.",
                    {
                        "from": {"type": "string", "description": "A name, company or address."},
                        "to": {"type": "string", "description": "A name or address they wrote to."},
                        "words": {
                            "type": "string",
                            "description": "Words in the email (subject or text). Only words likely to be in it.",
                        },
                        "when": {
                            "type": "string",
                            "description": "Their time words as said: today, yesterday, this week, last week, this month, last month, past 3 days, in October, since September, Monday.",
                        },
                        "after": {
                            "type": "string",
                            "description": "YYYY-MM-DD, when they gave an exact date.",
                        },
                        "before": {"type": "string", "description": "YYYY-MM-DD."},
                        "has_attachment": {
                            "type": "boolean",
                            "description": "A letter, file, document, PDF, photo or invoice they sent.",
                        },
                        "filename": {
                            "type": "string",
                            "description": "Part of a file name or a type: pdf, docx, jpg.",
                        },
                        "sent": {"type": "boolean", "description": "Only mail the user sent."},
                        "unread": {"type": "boolean"},
                        "query": {
                            "type": "string",
                            "description": "Raw Gmail search syntax, for anything the fields can't say.",
                        },
                        "max": {"type": "integer"},
                    },
                ),
                self.gmail_search,
                info=True,
            ),
            Tool(
                tool(
                    "gmail_people",
                    "Find who the user means by a name or role ('Sam', 'Nabiha Rahman', 'my professor', 'my "
                    "landlord') from the people they really email: sent mail and recent threads. Says if "
                    "there's one clear match (use it) or several (ask which).",
                    {
                        "name": {
                            "type": "string",
                            "description": "The name or role exactly as they said it.",
                        }
                    },
                    ["name"],
                ),
                self.gmail_people,
                info=True,
            ),
            Tool(
                tool(
                    "gmail_read",
                    "Read one email in full, with its attachment names.",
                    {"id": {"type": "string"}},
                    ["id"],
                ),
                self.gmail_read,
                info=True,
            ),
            Tool(
                tool(
                    "gmail_download",
                    "Save an email's attachment(s) to this PC (Documents/Pyxi). Returns the file paths.",
                    {
                        "id": {"type": "string"},
                        "filename": {
                            "type": "string",
                            "description": "Part of the file name; omit for all.",
                        },
                    },
                    ["id"],
                ),
                self.gmail_download,
                info=True,
            ),
            Tool(
                tool(
                    "gmail_send",
                    "Write an email or a reply (reply_to_id threads it and addresses it to the sender), optionally "
                    "attaching files from this PC. The user sees an approval card with who, the subject and "
                    "the gist, and approves it before it is sent.",
                    {
                        "to": {
                            "type": "string",
                            "description": "'Name <address>', comma separated for several. Omit for a reply to the sender.",
                        },
                        "subject": {"type": "string", "description": "Short. Omit for a reply."},
                        "gist": {
                            "type": "string",
                            "description": "One line on what the email says, e.g. 'Running 10 minutes late to the 3pm meeting.'",
                        },
                        "body": {"type": "string"},
                        "reply_to_id": {"type": "string"},
                        "attachments": {"type": "array", "items": {"type": "string"}},
                    },
                    ["body"],
                ),
                self.gmail_send,
            ),
            Tool(
                tool(
                    "calendar_list",
                    "What's on the user's Google Calendar soon.",
                    {"days": {"type": "number"}, "query": {"type": "string"}},
                ),
                self.calendar_list,
                info=True,
            ),
            Tool(
                tool(
                    "calendar_add",
                    "Add an event to the user's Google Calendar. start/end are local times like 2026-11-06T15:00 in the "
                    "user's time zone (see Now). Attendees get an invite; a Google Meet link is added when there are "
                    "attendees (meet=false to skip). The result reads the event back from Google: check it.",
                    {
                        "title": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "minutes": {"type": "integer"},
                        "attendees": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Exact email addresses the user gave.",
                        },
                        "location": {"type": "string"},
                        "description": {"type": "string"},
                        "meet": {
                            "type": "boolean",
                            "description": "Add a Google Meet link (default: yes when there are attendees).",
                        },
                    },
                    ["title", "start"],
                ),
                self.calendar_add,
            ),
            Tool(
                tool(
                    "docs_read",
                    "Read a Google Doc (URL or id).",
                    {"doc": {"type": "string"}},
                    ["doc"],
                ),
                self.docs_read,
                info=True,
            ),
            Tool(
                tool(
                    "docs_create",
                    "Create a Google Doc with text; returns its link.",
                    {"title": {"type": "string"}, "text": {"type": "string"}},
                    ["title"],
                ),
                self.docs_create,
            ),
            Tool(
                tool(
                    "sheets_read",
                    "Read a Google Sheet or Doc (a link, or words from its name) to answer a question "
                    "about it. To change one, use edit_document instead.",
                    {"sheet": {"type": "string"}},
                    ["sheet"],
                ),
                self.sheets_read,
                info=True,
            ),
        ]
