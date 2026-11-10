"""Background jobs: named agents doing real work while you keep talking to Pyxi.

* Browser agents run an Otto session (planning, guardrails, budgets, trajectories), with
  approvals and questions routed to the UI and live frames streamed to the panel.
* Writer agents draft text from the context they're handed.
* Explainer agents turn a topic into a 3D scene for the overlay.
* Files agents (Pixel) convert files: PDFs, images, Word to PDF, CSV and Excel. No model needed.
* Documents agents (Ledger) edit Excel, Word and PowerPoint files, and Google Sheets and Docs.
  Neither opens an app window or touches the user's mouse.
* Specialists (Atlas for travel, ...) each have a runner module, see ``agents/specialists.py``.

``then`` chains a handoff: when a job finishes, its result becomes the context of the next
agent's job. That's how "Scout, research Lumen, then Quill, draft a note to their CEO" works.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from otto.agent.runner import Otto
from otto.config import DEFAULT_SENSITIVE_PATTERNS
from otto.core.controls import RunControls
from otto.llm.base import LLMProvider, Message
from otto.storage import MemoryStore
from otto.types import AgentEvent
from pydantic import BaseModel, Field

from pyxi.agents.registry import AgentProfile, AgentRegistry
from pyxi.booking import CHECKIN_PATTERNS, CHECKIN_SAFE, CONFIRM_PATTERNS, HANDOVER
from pyxi.config import PyxiSettings
from pyxi.docs import SUMMARY_EXTS, SUPPORTED, document_mode, fix_document, summarize_document
from pyxi.hub import Hub
from pyxi.llm import otto_settings
from pyxi.safety import RULE as UNTRUSTED_RULE

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "failed", "stopped"]
LLMFactory = Callable[[AgentProfile], LLMProvider]
SceneMaker = Callable[[str, str, AgentProfile], Awaitable[dict[str, Any]]]


class Handoff(BaseModel):
    agent: str
    task: str


class Job(BaseModel):
    id: str = Field(default_factory=lambda: "job_" + secrets.token_hex(3))
    agent_id: str
    agent_name: str
    role: str
    task: str
    context: str = ""
    start_url: str | None = None
    files: list[str] = Field(default_factory=list)  # documents to work on (writers fix them)
    kind: str = "task"  # "booking": the final click is approved, payment is handed to the user
    # Pixel: {"operation": "compress_pdf", "options": {...}}; Ledger: {"google": [link or name]}
    params: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = "queued"
    summary: str = ""
    data: Any = None
    outputs: list[str] = Field(default_factory=list)
    run_id: str | None = None
    cost_usd: float = 0.0
    steps: int = 0
    then: Handoff | None = None
    parent: str | None = None
    created_at: float = Field(default_factory=time.time)
    ended_at: float | None = None

    def brief(self) -> str:
        extra = f" -> then {self.then.agent}" if self.then else ""
        return f"{self.id} {self.agent_name} [{self.status}] {self.task[:80]}{extra}" + (
            # On one line: a result is outside content, and a line of it must not be able to
            # start a line of the prompt ("# The user says").
            f": {' '.join(self.summary[:160].split())}" if self.summary else ""
        )


def _tally(changes: list[str]) -> list[str]:
    """'Added a paragraph' three times reads as 'Added 3 paragraphs'."""
    out: list[str] = []
    counts: dict[str, int] = {}
    for c in changes:
        counts[c] = counts.get(c, 0) + 1
    for c, n in counts.items():
        if n == 1:
            out.append(c)
            continue
        words = c.split(" ")
        if len(words) == 3 and words[1] in ("a", "an"):  # "Added a paragraph"
            out.append(f"{words[0]} {n} {words[2]}s")
        else:
            out.append(f"{c} (x{n})")
    return out


def plain_markdown(text: str) -> str:
    """A model's Markdown as plain text for a notification: no **bold**, `code` or # headings
    (the saved .md file keeps them). Bullets stay as "- "."""
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?!\w)", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+", "", text)
    return re.sub(r"(?m)^([ \t]*)[*+][ \t]+", r"\1- ", text).strip()


def describe_failure(exc: BaseException) -> str:
    """What went wrong, the way you'd tell a person (the notch shows this, never a stack)."""
    text = str(exc)
    low = text.lower()
    if type(exc).__name__ in ("PixelError", "LedgerError"):
        # Already said plainly, and it may name a file ("Budget 2026.xlsx is locked"), which
        # mustn't be mistaken for the spending limit below.
        return text.strip()[:300] or "That didn't work."
    if "browsertype.launch" in low or "executable doesn't exist" in low:
        return "I couldn't open a browser window on this PC. Restarting Pyxi usually fixes it."
    if re.search(r"\b401\b|authentication|invalid (x-)?api.?key", low):
        return "The AI model turned the key down. Check the model settings, then try again."
    if re.search(r"\b429\b|rate.?limit|overloaded", low):
        return "The AI model is busy right now. Try again in a minute."
    if "net::err_name_not_resolved" in low or "net::err_internet" in low:
        return "I couldn't reach that site. Is the internet connection up?"
    if "net::err" in low:
        return "That site wouldn't load."
    if "timeout" in low:
        return "The site took too long to respond."
    if type(exc).__name__ == "BudgetExceededError" or re.search(
        r"budget[ _](exceeded|exhausted|reached)|cost limit|max_cost", low
    ):
        return "I stopped before going over the spending limit for this job."
    if "switched off in Pyxi's Google Cloud project" in text:
        return text.strip()[:300]  # says what to turn on, with the link: keep it whole
    first = text.strip().splitlines()[0] if text.strip() else type(exc).__name__
    return first[:160]


# How Pyxi's browser teammates work. The user is waiting for a notification, so quick and
# right beats exhaustive: a found answer from a good source is a success, said plainly.
BROWSER_RULES = """How to work:
- Be quick: most jobs take 3-10 steps, and the user is waiting.
- One good search, open the best result, read or extract the answer, finish.
- Never repeat a search with small rewordings. After two searches that don't turn up anything
  better, finish with the best answer you have.
- A reputable source (the maker's own site, Wikipedia, a major newspaper or reference site)
  counts as reliable unless the user asked for something stricter. Name it in your summary.
- Finish with success=true whenever you found the answer, adding a short caveat if it
  matters. Use success=false only when you really could not find or do it.
- Start your summary with the result itself in one plain sentence (the pick and its price,
  the fact, what was done): it is read aloud. Then the details, a list one item per line."""


def plain_card(
    action: str, reason: str, *, booking: bool = False, checkin: bool = False
) -> tuple[str, str]:
    """Otto's confirm text ("click [8] "Book appointment"" / "... (matched '\\bbook\\b')") in
    words a person reads at a glance: Click "Book appointment" / This books it."""
    label = re.search(r'"([^"]+)"', action)
    what = f'Click "{label.group(1)}"' if label else action
    if checkin:
        return what, "This looks like a paid extra during check-in. Scout skips it unless you allow it."
    if booking:
        return (
            what,
            "This makes the booking with the details on that page. Check them in the window first.",
        )
    return what, re.sub(r"\s*\(matched .*\)\.?$", ".", reason)


class JobManager:
    def __init__(
        self,
        settings: PyxiSettings,
        registry: AgentRegistry,
        hub: Hub,
        *,
        llm_factory: LLMFactory,
        scene_maker: SceneMaker | None = None,
        otto_llm: LLMProvider | None = None,
        on_done: Callable[[Job], Awaitable[None]] | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.hub = hub
        self.llm_factory = llm_factory
        self.scene_maker = scene_maker
        self.otto_llm = otto_llm  # tests inject a scripted provider here
        self.on_done = on_done
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._controls: dict[str, RunControls] = {}
        self.memory = MemoryStore(settings.db_path)
        # Teammates work out of sight so nothing pops up over what you're doing; the task tray shows
        # their live view on request. Bookings stay visible: you may need to pay in that window.
        self.headed = False

    # ------------------------------------------------------------------ public api
    async def start(
        self,
        agent_ref: str,
        task: str,
        *,
        context: str = "",
        start_url: str | None = None,
        then: Handoff | None = None,
        parent: str | None = None,
        files: list[str] | None = None,
        kind: str = "task",
        params: dict[str, Any] | None = None,
    ) -> Job:
        agent = self.registry.get(agent_ref)
        if agent is None:
            raise ValueError(f"No agent called {agent_ref!r}.")
        if agent.role == "companion":
            raise ValueError("Pyxi can't delegate to itself; pick a teammate.")
        job = Job(
            agent_id=agent.id,
            agent_name=agent.name,
            role=agent.role,
            task=task,
            context=context,
            start_url=start_url,
            then=then,
            parent=parent,
            files=list(files or []),
            kind=kind,
            params=dict(params or {}),
        )
        self.jobs[job.id] = job
        await self._update(job)
        self._tasks[job.id] = asyncio.create_task(self._run(job, agent))
        return job

    async def track(self, agent_name: str, task: str, role: str = "errand") -> Job:
        """A job record for work the brain runs itself (errands), so it shows up like any other."""
        job = Job(
            agent_id=agent_name.lower(),
            agent_name=agent_name,
            role=role,
            task=task,
            status="running",
        )
        self.jobs[job.id] = job
        await self._update(job)
        return job

    async def settle(
        self, job: Job, *, ok: bool, summary: str, data: Any = None, cost: float = 0.0
    ) -> None:
        job.status, job.summary, job.data, job.cost_usd = (
            ("done" if ok else "failed"),
            summary,
            data,
            cost,
        )
        job.ended_at = time.time()
        await self._update(job)

    def active(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.status in {"queued", "running"}]

    def recent(self, n: int = 8) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created_at)[-n:]

    def control(self, job_id: str, action: str, text: str = "") -> bool:
        c = self._controls.get(job_id)
        if c is None:
            if action == "stop" and (task := self._tasks.get(job_id)):
                task.cancel()
                return True
            return False
        {"pause": c.pause, "resume": c.resume, "stop": c.stop}.get(
            action, lambda: c.send_message(text)
        )()
        return True

    async def stop_all(self) -> int:
        """Stop every teammate at once (the "Stop everything" hotkey): Otto's browsers are told to
        stop and their tasks cancelled, and every unfinished job shows as stopped."""
        stopped = 0
        for job_id, c in list(self._controls.items()):
            try:
                c.stop()
            except Exception:
                log.exception("stopping %s", job_id)
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        for job in list(self.jobs.values()):
            if job.status in ("queued", "running"):
                job.status = "stopped"
                job.summary = job.summary or "Stopped, as you asked."
                stopped += 1
                await self._update(job)
        return stopped

    async def wait(self, job_id: str) -> Job:
        task = self._tasks.get(job_id)
        if task:
            await asyncio.gather(task, return_exceptions=True)
        return self.jobs[job_id]

    async def wait_all(self) -> None:
        while pending := [t for t in self._tasks.values() if not t.done()]:
            await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------------------------ running
    async def _update(self, job: Job) -> None:
        if job.summary:
            from pyxi.logscrub import hide_secrets

            job.summary = hide_secrets(job.summary)  # a key read off the screen is never repeated
        await self.hub.emit("job", job=job.model_dump(mode="json"))
        if job.status in ("done", "failed", "stopped"):
            self._log_finished(job)

    def _log_finished(self, job: Job) -> None:
        """One line per finished job in .pyxi/jobs.jsonl (status, seconds, summary; never the
        files' contents), so a slow or failing teammate shows up when testing."""
        try:
            line = {
                "ts": time.time(),
                "id": job.id,
                "agent": job.agent_name,
                "status": job.status,
                "secs": round(time.time() - job.created_at, 1),
                "task": job.task[:160],
                "summary": (job.summary or "")[:300],
                "cost": round(job.cost_usd or 0, 4),
            }
            path = self.settings.data_dir / "jobs.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line) + chr(10))
        except Exception:  # a log line is never worth breaking a job
            log.debug("couldn't log the finished job", exc_info=True)

    async def _run(self, job: Job, agent: AgentProfile) -> None:
        job.status = "running"
        await self._update(job)
        try:
            if agent.role == "browser":
                await self._run_browser(job, agent)
            elif agent.role == "writer":
                await self._run_writer(job, agent)
            elif agent.role == "explainer":
                await self._run_explainer(job, agent)
            elif agent.role == "files":
                await self._run_files(job, agent)
            elif agent.role == "documents":
                await self._run_documents(job, agent)
            else:  # Atlas and the other specialists: agents/specialists.py
                from pyxi.agents.specialists import run as run_specialist

                await run_specialist(self, job, agent)
        except asyncio.CancelledError:
            job.status, job.summary = "stopped", job.summary or "Stopped."
        except Exception as exc:
            log.exception("job %s failed", job.id)
            job.status, job.summary = "failed", describe_failure(exc)
        job.ended_at = time.time()
        if job.status == "done" and job.outputs:
            # So the desktop app will open, reveal and drag these (and only files Pyxi made).
            from pyxi import made

            fixed = job.data.get("fixed", []) if isinstance(job.data, dict) else []
            made.record(self.settings.data_dir, [*job.outputs, *fixed])
        await self._update(job)
        if job.status == "done" and job.then:
            context = f"Result from {job.agent_name}: {job.summary}\n\nData: {json.dumps(job.data, default=str)[:4000]}"
            for path in job.outputs:
                try:
                    context += f"\n\nFile {path}:\n" + Path(path).read_text(encoding="utf-8")[:4000]
                except (OSError, ValueError):  # a PDF or a spreadsheet: the summary says enough
                    pass
            await self.hub.emit(
                "handoff", source=job.agent_name, target=job.then.agent, task=job.then.task
            )
            try:
                await self.start(job.then.agent, job.then.task, context=context, parent=job.id)
            except Exception:
                log.exception("hand-off from %s to %s failed", job.agent_name, job.then.agent)
        if self.on_done:
            await self.on_done(job)

    async def _run_browser(self, job: Job, agent: AgentProfile) -> None:
        booking = job.kind == "booking"
        safety: dict[str, Any] = {"confirm_policy": "ask"}
        if agent.allowed_domains:
            safety["allowed_domains"] = agent.allowed_domains
        if booking:  # "Book appointment" and "Reserve" are as final as "Submit": approve first
            safety["sensitive_action_patterns"] = [*DEFAULT_SENSITIVE_PATTERNS, *CONFIRM_PATTERNS]
        checkin = job.kind == "checkin"  # flight check-in: free steps go ahead, paid extras stop
        if checkin:
            safety["sensitive_action_patterns"] = [
                *(p for p in DEFAULT_SENSITIVE_PATTERNS if p not in CHECKIN_SAFE),
                *CHECKIN_PATTERNS,
            ]
        s = otto_settings(
            self.settings,
            model=self.settings.job_model,
            headless=not self.headed and not booking,  # they may need to pay in that window
            budget={
                "max_steps": 30 if booking else 25 if checkin else 18,
                "max_cost_usd": max(agent.max_cost_usd, 0.3) if booking else agent.max_cost_usd,
            },
            safety=safety,
            memory={"scope": agent.memory_scope},
        )

        async def confirm(action: str, reason: str) -> bool:
            action, reason = plain_card(action, reason, booking=booking, checkin=checkin)
            answer = await self.hub.request(
                "confirm", job_id=job.id, agent=agent.name, action=action, reason=reason
            )
            if answer and getattr(self, "audit", None) is not None:
                self.audit.add("clicked", f"Allowed: {action}", agent=agent.name, detail=reason)
            return bool(answer)

        async def ask(question: str) -> str | None:
            if booking and question.strip().upper().startswith(HANDOVER):
                # Their turn: pay (or sign in) in the browser window Scout has open. That can
                # take a while, so the card waits 15 minutes and just needs a Done.
                answer = await self.hub.request(
                    "ask",
                    timeout=900,
                    job_id=job.id,
                    agent=agent.name,
                    question=question.strip()[len(HANDOVER) :].strip(),
                    handover=True,
                )
                return (
                    "They pressed Done: they say they've finished in the browser. Reload the page "
                    "(navigate to the current address again) and look for the confirmation before "
                    "you finish."
                    if answer
                    else None
                )
            answer = await self.hub.request(
                "ask", job_id=job.id, agent=agent.name, question=question
            )
            return str(answer) if answer else None

        controls = RunControls(confirm_policy="ask", confirm_handler=confirm, ask_handler=ask)
        controls.want_frames = True

        async def relay(event: AgentEvent) -> None:
            if event.type == "frame":
                await self.hub.emit("job_frame", job_id=job.id, image=event.data["image"])
                return
            if event.type == "run_started":
                job.run_id = event.run_id
            if event.type == "step_finished":
                job.steps = event.data["step"]
                job.cost_usd = event.data["budget"]["cost_usd"]
            await self.hub.emit(
                "job_event", job_id=job.id, agent=agent.name, event=event.type, data=event.data
            )

        controls.subscribe(relay)
        self._controls[job.id] = controls
        # Bookings, forms and check-ins carry their own rules.
        rules = "" if booking or checkin else "\n\n" + BROWSER_RULES
        task = f"{job.task}\n\nYou are {agent.name}: {agent.persona}{rules}"
        if job.context:
            task += f"\n\nContext from your teammates:\n{job.context}"
        otto = Otto(s, llm=self.otto_llm, controls=controls)
        try:
            result = await otto.run(
                task, start_url=job.start_url, workflow=f"pyxi:{agent.id}", tags=["pyxi", agent.id]
            )
        finally:
            self._controls.pop(job.id, None)
        job.run_id, job.summary, job.data = result.run_id, result.summary, result.data
        job.outputs, job.cost_usd, job.steps = result.outputs, result.usage.cost_usd, result.steps
        job.status = (
            "done"
            if result.success
            else ("stopped" if result.status.value == "stopped" else "failed")
        )

    async def _run_writer(self, job: Job, agent: AgentProfile) -> None:
        llm = self.llm_factory(agent)
        mode = document_mode(job.task)  # fix, translate, rewrite or summarize
        exts = SUMMARY_EXTS if mode == "summarize" else SUPPORTED
        docs = [Path(f) for f in job.files if Path(f).suffix.lower() in exts]
        if docs and mode == "summarize":
            await self._summarize_documents(job, docs, llm)
            return
        if docs:
            await self._fix_documents(job, docs, llm, mode)
            return
        memories = "\n".join(
            m.render() for m in self.memory.search(agent.memory_scope, job.task, 5)
        )
        prompt = f"Task: {job.task}"
        if job.context:
            prompt += f"\n\nContext:\n{job.context}"
        if memories:
            prompt += f"\n\nThings you remember:\n{memories}"
        response = await llm.complete(
            system=f"You are {agent.name}, a writer on the user's team. {agent.persona} "
            "Write the deliverable itself, ready to use, with no preamble. Never invent facts that are not in the context."
            + UNTRUSTED_RULE,
            messages=[Message.user(prompt)],
            max_tokens=4000,
        )
        # No em dashes in what they send, even when the model slips one in (a spaced en dash
        # is the same thing; "9-5" style ranges are left alone).
        text = re.sub(r"\s*—\s*|\s+–\s+", ", ", response.text).strip()
        if not text:
            raise RuntimeError("The writer returned nothing.")
        out_dir = self.settings.data_dir / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{job.id}-{agent.id}.md"
        path.write_text(text, encoding="utf-8")
        job.summary, job.data, job.outputs = text, {"draft": text}, [str(path)]
        job.cost_usd, job.status = response.usage.cost_usd, "done"

    async def _summarize_documents(self, job: Job, docs: list[Path], llm: LLMProvider) -> None:
        """A summary of each document, saved beside it as "<name> (summary).md"."""
        parts, outputs, cost = [], [], 0.0
        for doc in docs:
            await self._status(job, f"Reading {doc.name}")
            result = await summarize_document(doc, job.task, llm)
            cost += result.cost_usd
            outputs.append(str(result.path))
            text = plain_markdown(result.text)  # the notification is plain text: no **bold**
            parts.append(text if len(docs) == 1 else f"{doc.name}: {text}")
        job.summary = "\n\n".join(parts)
        job.data = {"draft": job.summary, "outputs": outputs}
        job.outputs, job.cost_usd, job.status = outputs, cost, "done"

    async def _fix_documents(
        self, job: Job, docs: list[Path], llm: LLMProvider, mode: str = "fix"
    ) -> None:
        """Fix, translate or rewrite each document and save a copy beside it; the originals are
        never touched."""
        lines, outputs, cost = [], [], 0.0
        all_changes: list[str] = []
        all_notes: list[str] = []
        for doc in docs:
            await self.hub.emit(
                "job_event",
                job_id=job.id,
                agent=job.agent_name,
                event="status",
                data={"message": f"{'Translating' if mode == 'translate' else 'Editing'} {doc.name}"},
            )
            result = await fix_document(doc, job.task, llm, mode=mode)
            cost += result.cost_usd
            outputs.append(str(result.path))
            all_changes += result.changes
            all_notes += result.notes
            notes = "; ".join(result.changes[:5])
            verb = {"translate": "translated", "rewrite": "rewrote"}.get(mode, "fixed")
            if result.listed:
                lines.append(
                    f"{doc.name} is a PDF, and a PDF's layout can't be edited, so nothing in it "
                    f"was changed or typed out again. {result.changed} correction"
                    f"{'' if result.changed == 1 else 's'} to make in the file it was made "
                    f"from, listed in {result.path}" + (f": {notes}" if notes else ".")
                )
                continue
            lines.append(
                f"{doc.name}: {verb} {result.changed} of {result.total} paragraphs, saved as {result.path}"
                + (f". Notable: {notes}" if notes else "")
            )
        job.summary = " ".join(lines)
        key = "fixed" if mode == "fix" else "outputs"  # "fixed" makes it a "Your document is fixed" card
        job.data = {key: outputs, "changes": all_changes[:12], "notes": all_notes[:5]}
        job.outputs, job.cost_usd, job.status = outputs, cost, "done"

    async def _run_explainer(self, job: Job, agent: AgentProfile) -> None:
        if self.scene_maker is None:
            raise RuntimeError("No scene maker configured.")
        scene = await self.scene_maker(job.task, job.context, agent)
        job.summary = f"Built a 3D scene: {scene.get('title', 'untitled')}."
        job.data, job.status = {"scene_id": scene.get("id")}, "done"

    async def _status(self, job: Job, message: str) -> None:
        await self.hub.emit(
            "job_event",
            job_id=job.id,
            agent=job.agent_name,
            event="status",
            data={"message": message},
        )

    async def _run_files(self, job: Job, agent: AgentProfile) -> None:
        """Pixel: one conversion, straight on the files, in a worker thread. No model call."""
        from pyxi.agents import pixel

        op = str(job.params.get("operation") or "") or pixel.guess_operation(job.task, job.files)
        if not op:
            raise RuntimeError(
                "I couldn't tell which conversion you want: PDF, images, smaller, merge or split?"
            )
        names = ", ".join(Path(f).name for f in job.files[:3]) or "your files"
        # Passwords and saved details never come from the model: a password is typed on a card,
        # and form details are read from this PC. Neither is kept on the job.
        options = {
            k: v
            for k, v in (job.params.get("options") or {}).items()
            if k not in ("password", "details", "out_dir")
        }
        if op in pixel.NEEDS_PASSWORD:
            password = await self._ask_password(job, agent, op, names)
            if not password:
                job.status, job.summary = "stopped", "No password, so I left it as it is."
                return
            options["password"] = password
        if op == "fill_form":
            from pyxi import booking

            options["details"] = booking.load(self.settings.data_dir)
        note = ""
        if op in ("to_pdf", "word_to_pdf") and any(
            Path(f).suffix.lower() in pixel.OFFICE for f in job.files
        ):
            got = await self._office_converter(job, agent, names)
            if got is None:
                return  # an old Office file, and no converter: the job says why
            note = got
        await self._status(job, f"Working on {names}")
        if op in ("extract_text", "make_searchable", "pdf_to_word"):
            from pyxi.agents import pixel_ocr

            if not pixel_ocr.windows_available() and pixel_ocr.pack_pending():
                # No Windows OCR here, and the voice pack (which brings RapidOCR's engine) is
                # still on its way: the scan waits for it, so say why.
                await self._status(job, "Getting the text reader first (about 75 MB, once)")
        result = await asyncio.to_thread(pixel.run, op, job.files, options)
        options.pop("password", None)
        job.outputs = [str(p) for p in result.outputs]
        job.summary = result.summary
        if note:
            job.summary = job.summary.replace(pixel.BUILT_IN_NOTE, "") + note
        if job.outputs:
            where = Path(job.outputs[0]).parent
            job.summary += f" It's in {where}." if len(job.outputs) == 1 else f" They're in {where}."
        job.data = {"operation": op, "outputs": job.outputs, **result.details}
        job.status = "done"

    async def _ask_password(self, job: Job, agent: AgentProfile, op: str, names: str) -> str:
        """The user types the password on a masked card; it goes straight to Pixel."""
        question = (
            f"Type the password to lock {names} with. You'll need it to open the PDF."
            if op == "protect_pdf"
            else f"Type the password for {names}."
        )
        answer = await self.hub.request(
            "ask", timeout=300, job_id=job.id, agent=agent.name, question=question, secret=True
        )
        return str(answer or "")

    def converter(self) -> Any:
        """LibreOffice on demand (agents/libreoffice.py); progress goes to Settings."""
        if getattr(self, "_converter", None) is None:
            from pyxi.agents.libreoffice import Installer

            self._converter = Installer()

        async def tell(status: Any) -> None:
            await self.hub.emit("libreoffice", **status.public())

        self._converter.listen(tell)
        return self._converter

    async def _office_converter(self, job: Job, agent: AgentProfile, names: str) -> str | None:
        """Before an Office file becomes a PDF: "" to go ahead, a note for the summary, or None
        when it can't be done (an old .doc/.xls/.ppt and no LibreOffice)."""
        from pyxi.agents import libreoffice, pixel

        if pixel.libreoffice():
            return ""
        old = [f for f in job.files if Path(f).suffix.lower() in pixel.OFFICE - pixel.BUILT_IN]
        choice = libreoffice.load_choice(self.settings.data_dir)
        if not choice.asked:
            ok = await self.hub.request(
                "confirm",
                timeout=300,
                job_id=job.id,
                agent=agent.name,
                action=f"Download the free LibreOffice converter (about {libreoffice.APPROX_MB} MB)?",
                reason="Once, from The Document Foundation, into Pyxi's own folder: no admin rights, "
                "and nothing else on your PC changes. Then Word, Excel and PowerPoint files keep "
                "their exact layout as PDFs, and old .doc, .xls and .ppt files work too.",
            )
            choice = libreoffice.save_choice(
                self.settings.data_dir, libreoffice.Choice(asked=True, allowed=bool(ok))
            )
            await self.hub.emit("libreoffice", **self.converter().status.public(), **vars(choice))
        if choice.allowed:
            installer = self.converter()
            installer.start()
            if not old:
                return " Exact layouts from next time: LibreOffice is downloading in the background."

            async def relay(status: Any) -> None:
                pct = f" ({status.progress:.0%})" if status.state == "downloading" else ""
                await self._status(job, f"{status.message}{pct}")

            installer.listen(relay)
            try:
                soffice = await installer.wait()
            finally:
                installer.unlisten(relay)
            if soffice is None:
                job.status = "failed"
                job.summary = installer.status.message or "LibreOffice couldn't be set up."
                return None
            return ""
        if old:
            job.status = "failed"
            job.summary = (
                f"{Path(old[0]).name} is an old Office file, and turning it into a PDF needs the free "
                "LibreOffice converter. Turn it on in Settings, under Your files, and ask again."
            )
            return None
        return ""

    def _google(self) -> Any:
        if getattr(self, "google", None) is None:
            from pyxi.connectors.google import GoogleConnector

            self.google = GoogleConnector(
                self.settings.data_dir / "connectors", Path.home() / "Documents" / "Pyxi"
            )
        return self.google

    async def _run_documents(self, job: Job, agent: AgentProfile) -> None:
        """Ledger: edit Excel, Word and PowerPoint files (a copy beside each) or Google files."""
        from pyxi.agents.ledger import run_ledger

        llm = self.llm_factory(agent)

        async def ask(system: str, prompt: str) -> tuple[str, float]:
            response = await llm.complete(
                system=system + UNTRUSTED_RULE, messages=[Message.user(prompt)], max_tokens=8000
            )
            return response.text, response.usage.cost_usd

        async def progress(line: str) -> None:
            await self._status(job, line[:1].upper() + line[1:])

        google_refs = [str(g) for g in job.params.get("google") or [] if str(g).strip()]
        result = await run_ledger(
            job.task,
            files=job.files,
            google=self._google() if google_refs else None,
            google_refs=google_refs,
            ask=ask,
            name=agent.name,
            persona=agent.persona,
            context=job.context,
            progress=progress,
        )
        job.cost_usd = result.cost_usd
        job.outputs = result.outputs
        job.data = {
            "changes": result.changes[:12],
            "notes": result.problems[:3],
            "links": result.links,
            "outputs": result.outputs,
        }
        if not result.changes:
            job.status = "failed"
            job.summary = result.summary or "I couldn't make that change."
            return
        # One sentence for the edits: the notification shows the first two sentences, so the
        # edits that failed and where the copy went are never cut off by a list of edits.
        done = _tally(result.changes)[:4]
        first = "; ".join(
            [done[0]] + [c[:1].lower() + c[1:] if c[1:2].islower() else c for c in done[1:]]
        )
        if result.problems:
            n = len(result.problems)
            first += f", but {n} edit{'s' if n != 1 else ''} didn't work"
        parts = [first.rstrip(".") + "."]
        if result.outputs:
            names = ", ".join(Path(o).name for o in result.outputs)
            parts.append(f"Saved as {names}, beside the original.")
        made = any(str(g).lower().startswith("new ") for g in job.params.get("google") or [])
        if result.links and made:
            parts.append("It's a new file in your Google Drive: " + " ".join(result.links))
        elif result.links:
            parts.append("Edited in Google (File > Version history has the old one): " + " ".join(result.links))
        job.summary = " ".join(parts)
        job.status = "done"
