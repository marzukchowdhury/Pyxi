"""The specialist teammates' jobs: one runner per role, looked up by :class:`JobManager`.

Each specialist lives in its own module (``pyxi.agents.atlas`` for travel, ...) with a
``run(manager, job, agent)`` coroutine that fills in ``job.summary``, ``job.data`` and
``job.status``. A runner can put its own notification in ``job.data``: ``title`` and ``body``
(and ``changes``, ``card`` for the tray's detail view), and the brain shows that instead of the
generic "Atlas finished".
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyxi.agents.jobs import Job, JobManager
    from pyxi.agents.registry import AgentProfile

# role -> module with run(manager, job, agent)
RUNNERS = {
    "travel": "pyxi.agents.atlas",
    "money": "pyxi.agents.penny",
    "meetings": "pyxi.agents.scribe",
    "study": "pyxi.agents.sage",
    "coder": "pyxi.agents.patch",
    "automation": "pyxi.agents.ditto",
    "memory": "pyxi.agents.trace",
}


async def run(manager: JobManager, job: Job, agent: AgentProfile) -> None:
    module = RUNNERS.get(agent.role)
    if module is None:
        raise RuntimeError(f"{agent.name} doesn't know how to do that yet.")
    await importlib.import_module(module).run(manager, job, agent)
