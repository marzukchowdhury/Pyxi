"""Background jobs: named agents doing real work while you keep talking to Wisp.

* Browser agents run an Otto session (planning, guardrails, budgets, trajectories), with
  approvals and questions routed to the UI and live frames streamed to the panel.
* Writer agents draft text from the context they're handed.
* Explainer agents turn a topic into a 3D scene for the overlay.

``then`` chains a handoff: when a job finishes, its result becomes the context of the next
agent's job. That's how "Scout, research Lumen, then Quill, draft a note to their CEO" works.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from otto.agent.runner import Otto
from otto.core.controls import RunControls
from otto.llm.base import LLMProvider, Message
from otto.storage import MemoryStore
from otto.types import AgentEvent
from pydantic import BaseModel, Field

from wisp.agents.registry import AgentProfile, AgentRegistry
from wisp.config import WispSettings
from wisp.docs import SUPPORTED, fix_document
from wisp.hub import Hub
from wisp.llm import otto_settings

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
            f": {self.summary[:160]}" if self.summary else ""
        )


class JobManager:
    def __init__(
        self,
        settings: WispSettings,
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
        self.headed = True  # watching the browser is half the fun

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
    ) -> Job:
        agent = self.registry.get(agent_ref)
        if agent is None:
            raise ValueError(f"No agent called {agent_ref!r}.")
        if agent.role == "companion":
            raise ValueError("Wisp can't delegate to itself; pick a teammate.")
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
        )
        self.jobs[job.id] = job
        await self._update(job)
        self._tasks[job.id] = asyncio.create_task(self._run(job, agent))
        return job

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
        await self.hub.emit("job", job=job.model_dump(mode="json"))

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
        except asyncio.CancelledError:
            job.status, job.summary = "stopped", job.summary or "Stopped."
        except Exception as exc:
            log.exception("job %s failed", job.id)
            job.status, job.summary = "failed", f"{type(exc).__name__}: {exc}"
        job.ended_at = time.time()
        await self._update(job)
        if job.status == "done" and job.then:
            context = f"Result from {job.agent_name}: {job.summary}\n\nData: {json.dumps(job.data, default=str)[:4000]}"
            for path in job.outputs:
                try:
                    context += f"\n\nFile {path}:\n" + Path(path).read_text(encoding="utf-8")[:4000]
                except OSError:
                    pass
            await self.hub.emit(
                "handoff", source=job.agent_name, target=job.then.agent, task=job.then.task
            )
            await self.start(job.then.agent, job.then.task, context=context, parent=job.id)
        if self.on_done:
            await self.on_done(job)

    async def _run_browser(self, job: Job, agent: AgentProfile) -> None:
        safety: dict[str, Any] = {"confirm_policy": "ask"}
        if agent.allowed_domains:
            safety["allowed_domains"] = agent.allowed_domains
        s = otto_settings(
            self.settings,
            model=self.settings.job_model,
            headless=not self.headed,
            budget={"max_steps": 30, "max_cost_usd": agent.max_cost_usd},
            safety=safety,
            memory={"scope": agent.memory_scope},
        )

        async def confirm(action: str, reason: str) -> bool:
            answer = await self.hub.request(
                "confirm", job_id=job.id, agent=agent.name, action=action, reason=reason
            )
            return bool(answer)

        async def ask(question: str) -> str | None:
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
        task = f"{job.task}\n\nYou are {agent.name}: {agent.persona}"
        if job.context:
            task += f"\n\nContext from your teammates:\n{job.context}"
        otto = Otto(s, llm=self.otto_llm, controls=controls)
        try:
            result = await otto.run(
                task, start_url=job.start_url, workflow=f"wisp:{agent.id}", tags=["wisp", agent.id]
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
        docs = [Path(f) for f in job.files if Path(f).suffix.lower() in SUPPORTED]
        if docs:
            await self._fix_documents(job, docs, llm)
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
            "Write the deliverable itself, ready to use, with no preamble. Never invent facts that are not in the context.",
            messages=[Message.user(prompt)],
            max_tokens=4000,
        )
        text = response.text.strip()
        if not text:
            raise RuntimeError("The writer returned nothing.")
        out_dir = self.settings.data_dir / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{job.id}-{agent.id}.md"
        path.write_text(text, encoding="utf-8")
        job.summary, job.data, job.outputs = text, {"draft": text}, [str(path)]
        job.cost_usd, job.status = response.usage.cost_usd, "done"

    async def _fix_documents(self, job: Job, docs: list[Path], llm: LLMProvider) -> None:
        """Fix each document and save a copy beside it; the originals are never touched."""
        lines, outputs, cost = [], [], 0.0
        for doc in docs:
            await self.hub.emit(
                "job_event",
                job_id=job.id,
                agent=job.agent_name,
                event="status",
                data={"message": f"Editing {doc.name}"},
            )
            result = await fix_document(doc, job.task, llm)
            cost += result.cost_usd
            outputs.append(str(result.path))
            notes = "; ".join(result.changes[:5])
            lines.append(
                f"{doc.name}: fixed {result.changed} of {result.total} paragraphs, saved as {result.path}"
                + (f". Notable: {notes}" if notes else "")
            )
        job.summary = " ".join(lines)
        job.data = {"fixed": outputs}
        job.outputs, job.cost_usd, job.status = outputs, cost, "done"

    async def _run_explainer(self, job: Job, agent: AgentProfile) -> None:
        if self.scene_maker is None:
            raise RuntimeError("No scene maker configured.")
        scene = await self.scene_maker(job.task, job.context, agent)
        job.summary = f"Built a 3D scene: {scene.get('title', 'untitled')}."
        job.data, job.status = {"scene_id": scene.get("id")}, "done"
