"""Named agents and the job runner that lets them work (and hand off) in the background."""

from wisp.agents.jobs import Job, JobManager
from wisp.agents.registry import DEFAULT_ROSTER, AgentProfile, AgentRegistry

__all__ = ["DEFAULT_ROSTER", "AgentProfile", "AgentRegistry", "Job", "JobManager"]
