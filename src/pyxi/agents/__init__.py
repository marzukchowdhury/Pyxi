"""Named agents and the job runner that lets them work (and hand off) in the background."""

from pyxi.agents.jobs import Job, JobManager
from pyxi.agents.registry import DEFAULT_ROSTER, AgentProfile, AgentRegistry

__all__ = ["DEFAULT_ROSTER", "AgentProfile", "AgentRegistry", "Job", "JobManager"]
