"""Google: Gmail, Calendar and Docs with one sign-in.

Setup (once): create a free Google Cloud project, enable the Gmail, Calendar and Docs APIs,
make an OAuth client of type "Desktop app", and save its JSON as ``.wisp/google_client.json``
(or set ``GOOGLE_CLIENT_ID`` and ``GOOGLE_CLIENT_SECRET``). Then ``wisp connect google`` (or the
Connect button in Settings) opens Google's sign-in in your browser. Wisp never sees your
password; Google hands back a token that stays in ``.wisp/connectors/google.json``.
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
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Any

import httpx

from wisp.connectors import Connector, Outgoing, Result, Status, Tool, tool
from wisp.timecontext import zone

EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _clock(t: datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary"
DOCS = "https://docs.googleapis.com/v1/documents"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:120] or "attachment"


class GoogleConnector(Connector):
    id = "google"
    name = "Google (Gmail, Calendar, Docs)"

    def __init__(self, store: Path, downloads: Path) -> None:
        self.token_path = store / "google.json"
        self.client_path = store.parent / "google_client.json"
        self.downloads = downloads
        self._creds = None
        self.email: str | None = None
        self._tz: str | None = None

    # ------------------------------------------------------------------ auth
    def client_config(self) -> dict[str, Any] | None:
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
        return None

    def connected(self) -> bool:
        return self.token_path.exists()

    def status(self) -> Status:
        if self.connected():
            return Status(self.id, self.name, True, self._saved_email() or "Connected")
        setup = [
            "Open console.cloud.google.com and create a project (free).",
            "Enable the Gmail API, Google Calendar API and Google Docs API.",
            "OAuth consent screen: External, add yourself as a test user.",
            "Credentials > Create OAuth client > Desktop app > Download JSON.",
            "Save it as wisp/.wisp/google_client.json, then press Connect.",
        ]
        detail = (
            "Ready to sign in"
            if self.client_config()
            else "Needs a Google OAuth client (5 minutes, free)"
        )
        return Status(self.id, self.name, False, detail, setup)

    def _saved_email(self) -> str | None:
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8")).get("wisp_email")
        except (OSError, ValueError):
            return None

    def sign_in(self) -> str:
        """Run Google's sign-in in the browser (blocking; call in a thread)."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        config = self.client_config()
        if config is None:
            raise RuntimeError("No Google OAuth client yet: save it as .wisp/google_client.json.")
        flow = InstalledAppFlow.from_client_config(config, SCOPES)
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            prompt="consent",
            success_message="Wisp is connected to Google. You can close this tab.",
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
        data["wisp_email"] = email
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(data), encoding="utf-8")
        self._creds = None
        return email or "your Google account"

    def disconnect(self) -> None:
        self.token_path.unlink(missing_ok=True)
        self._creds = None

    async def _token(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if self._creds is None:
            self._creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
        if not self._creds.valid:
            await asyncio.to_thread(self._creds.refresh, Request())
            data = json.loads(self._creds.to_json())
            data["wisp_email"] = self._saved_email()
            self.token_path.write_text(json.dumps(data), encoding="utf-8")
        return self._creds.token

    async def _api(self, method: str, url: str, **kw: Any) -> Any:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.request(method, url, headers=headers, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Google said {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

    # ------------------------------------------------------------------ gmail
    async def gmail_search(self, args: dict[str, Any]) -> Result:
        q = str(args.get("query", "")).strip()
        listing = await self._api(
            "GET",
            f"{GMAIL}/messages",
            params={"q": q, "maxResults": min(int(args.get("max", 6) or 6), 10)},
        )
        ids = [m["id"] for m in listing.get("messages", [])]
        if not ids:
            return f"gmail_search: nothing matched {q!r}."
        metas = await asyncio.gather(
            *(self._api("GET", f"{GMAIL}/messages/{i}", params={"format": "full"}) for i in ids)
        )
        return "Emails (newest first):\n" + "\n".join(self._summary(m) for m in metas)

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

    async def gmail_read(self, args: dict[str, Any]) -> Result:
        msg = await self._api("GET", f"{GMAIL}/messages/{args['id']}", params={"format": "full"})
        h = self._headers(msg)
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
        """Prepare an email (new or a reply, optionally with files) and ask before sending."""
        to = str(args.get("to", "")).strip()
        subject = str(args.get("subject", "")).strip()
        body = str(args.get("body", "")).strip()
        files = [Path(p) for p in args.get("attachments", []) or []]
        missing = [str(p) for p in files if not p.is_file()]
        if missing:
            return f"gmail_send: can't find {missing}."
        thread_id = None
        msg = EmailMessage()
        if args.get("reply_to_id"):
            orig = await self._api(
                "GET", f"{GMAIL}/messages/{args['reply_to_id']}", params={"format": "metadata"}
            )
            h = self._headers(orig)
            thread_id = orig.get("threadId")
            to = to or parseaddr(h.get("reply-to") or h.get("from", ""))[1]
            subject = subject or (
                "Re: " + re.sub(r"^(re:\s*)+", "", h.get("subject", ""), flags=re.I)
            )
            if h.get("message-id"):
                msg["In-Reply-To"] = h["message-id"]
                msg["References"] = (h.get("references", "") + " " + h["message-id"]).strip()
        if not to or "@" not in to:
            return "gmail_send: who should it go to?"
        msg["To"], msg["Subject"] = to, subject
        msg.set_content(body)
        for p in files:
            ctype, _ = mimetypes.guess_type(p.name)
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
        raw = {"raw": _b64url(msg.as_bytes()), **({"threadId": thread_id} if thread_id else {})}

        async def send() -> str:
            # Sent only on approval; a declined email leaves nothing behind (no stray draft).
            await self._api("POST", f"{GMAIL}/messages/send", json=raw)
            return f"Sent to {to}."

        attached = f"\nAttached: {', '.join(p.name for p in files)}" if files else ""
        return Outgoing(
            action=f"Send an email to {to}",
            preview=f"Subject: {subject}\n\n{body[:600]}{'...' if len(body) > 600 else ''}{attached}",
            run=send,
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
            except Exception:
                self._tz = ""
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
            "start": {
                "dateTime": start.replace(tzinfo=None).isoformat(timespec="seconds"),
                "timeZone": tz_name or str(start.tzinfo),
            },
            "end": {
                "dateTime": end.replace(tzinfo=None).isoformat(timespec="seconds"),
                "timeZone": tz_name or str(end.tzinfo),
            },
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
        return Outgoing(
            action=f"Invite {', '.join(guests)} to '{event['summary']}'",
            preview=f"{when}" + (" with a Google Meet link" if meet else ""),
            run=create,
            kind="invite",
            recipients=guests,
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
                    "Search the user's Gmail (Gmail search syntax, e.g. 'from:sam has:attachment newer_than:7d').",
                    {"query": {"type": "string"}, "max": {"type": "integer"}},
                    ["query"],
                ),
                self.gmail_search,
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
                    "Save an email's attachment(s) to this PC (Documents/Wisp). Returns the file paths.",
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
                    "Write an email or a reply (reply_to_id threads it), optionally attaching files from this PC. The user always sees and approves it before it is sent.",
                    {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
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
        ]
