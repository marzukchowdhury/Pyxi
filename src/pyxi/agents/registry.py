"""Persistent named agents: a roster of teammates with personalities, voices and memories.

Each agent has a *role* that decides what it can do:

* ``companion`` talks with you, sees the screen, points and explains (Pyxi itself).
* ``browser`` drives a real browser through Otto (research, forms, lookups).
* ``writer`` drafts text from context (messages, summaries, emails). No browser access.
* ``explainer`` turns a topic into a 3D scene.
* ``files`` converts files: PDFs, images, Word to PDF, CSV and Excel (Pixel).
* ``documents`` edits Excel, Word and PowerPoint files and Google Sheets and Docs (Ledger).
* ``travel`` builds trip itineraries from the confirmations in the user's mail (Atlas).

Agents keep their own long-term memory (Otto's FTS5 memory store, scoped per agent), and
any agent can hand its result to another (Scout researches, then Quill writes).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Role = Literal[
    "companion", "browser", "writer", "explainer", "files", "documents", "travel"
]


class AgentProfile(BaseModel):
    id: str
    name: str
    role: Role
    persona: str = Field(description="Personality and working style, in a sentence or two.")
    voice: str = "af_heart"
    color: str = "#9ecbff"
    skills: list[str] = Field(
        default_factory=list, description="Short tags shown on the agent card."
    )
    allowed_domains: list[str] = Field(
        default_factory=list, description="Browser agents only. Empty = any."
    )
    max_cost_usd: float = 1.0
    builtin: bool = False
    created_at: float = Field(default_factory=time.time)

    @property
    def memory_scope(self) -> str:
        return f"agent:{self.id}"


DEFAULT_ROSTER = [
    AgentProfile(
        id="pyxi",
        name="Pyxi",
        role="companion",
        voice="af_heart",
        color="#ffc861",
        builtin=True,
        persona="Warm, curious and quietly hilarious: dry, observational humour, never mean. Explains with "
        "pictures before paragraphs, keeps answers short, and can't resist a good 3D model.",
        skills=["sees your screen", "points & draws", "3D explanations", "coordinates the team"],
    ),
    AgentProfile(
        id="scout",
        name="Scout",
        role="browser",
        voice="am_puck",
        color="#7cc7ff",
        builtin=True,
        persona="Quick, methodical researcher. Prefers official sources, saves facts with links, never guesses.",
        skills=["web research", "forms", "price checks"],
        max_cost_usd=1.5,
    ),
    AgentProfile(
        id="quill",
        name="Quill",
        role="writer",
        voice="bf_emma",
        color="#f0a9c9",
        builtin=True,
        persona="Clear, friendly writer. Short sentences, specific details, no fluff and no em dashes.",
        skills=["outreach messages", "summaries", "emails"],
        max_cost_usd=0.3,
    ),
    AgentProfile(
        id="sketch",
        name="Sketch",
        role="explainer",
        voice="am_michael",
        color="#86e0c3",
        builtin=True,
        persona="Visual thinker. Turns any idea into a tidy 3D scene with a five-step tour.",
        skills=["3D diagrams", "exploded views", "flow animations"],
        max_cost_usd=0.4,
    ),
    AgentProfile(
        id="pixel",
        name="Pixel",
        role="files",
        voice="af_kore",
        color="#b9a3ff",
        builtin=True,
        persona="Tidy, fast file wrangler. Converts, merges, splits and shrinks files in seconds, "
        "never touches the original, and says exactly where the new one is.",
        skills=["image to PDF", "merge, split, shrink PDFs", "Word to PDF", "CSV and Excel"],
        max_cost_usd=0.05,
    ),
    AgentProfile(
        id="ledger",
        name="Ledger",
        role="documents",
        voice="bm_george",
        color="#8fd694",
        builtin=True,
        persona="Careful numbers person. Edits spreadsheets, documents and decks directly, writes "
        "real formulas instead of pasted numbers, and keeps your original safe.",
        skills=["Excel formulas & totals", "pivot summaries", "Word & PowerPoint", "Google Sheets & Docs"],
        max_cost_usd=0.3,
    ),
    AgentProfile(
        id="atlas",
        name="Atlas",
        role="travel",
        voice="am_fenrir",
        color="#ffb27a",
        builtin=True,
        persona="Calm, organised travel planner. Reads your flight and hotel confirmations, lays "
        "the trip out day by day, and reminds you before you need to leave.",
        skills=["itineraries from your mail", "flight reminders", "hotel addresses", "check-in"],
        max_cost_usd=0.05,
    ),
]


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "agent"


class AgentRegistry:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, profile TEXT NOT NULL)"
            )
            for agent in DEFAULT_ROSTER:
                c.execute(
                    "INSERT OR IGNORE INTO agents (id, profile) VALUES (?, ?)",
                    (agent.id, agent.model_dump_json()),
                )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def all(self) -> list[AgentProfile]:
        with self._conn() as c:
            rows = c.execute("SELECT profile FROM agents").fetchall()
        agents = [AgentProfile.model_validate_json(r[0]) for r in rows]
        return sorted(agents, key=lambda a: (not a.builtin, a.created_at))

    def get(self, ref: str) -> AgentProfile | None:
        ref = ref.strip().lower()
        return next((a for a in self.all() if a.id == ref or a.name.lower() == ref), None)

    def save(self, agent: AgentProfile) -> AgentProfile:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO agents (id, profile) VALUES (?, ?)",
                (agent.id, agent.model_dump_json()),
            )
        return agent

    def create(self, name: str, role: Role, persona: str, **extra: object) -> AgentProfile:
        base = slugify(name)
        existing = {a.id for a in self.all()}
        agent_id, n = base, 2
        while agent_id in existing:
            agent_id, n = f"{base}-{n}", n + 1
        return self.save(
            AgentProfile(
                id=agent_id,
                name=name.strip()[:32],
                role=role,
                persona=persona.strip()[:600],
                **extra,
            )
        )

    def delete(self, agent_id: str) -> bool:
        agent = self.get(agent_id)
        if agent is None or agent.builtin:
            return False
        with self._conn() as c:
            c.execute("DELETE FROM agents WHERE id = ?", (agent.id,))
        return True

    def roster_text(self) -> str:
        return "\n".join(
            f"- {a.name} (id {a.id}, {a.role}): {a.persona} Skills: {', '.join(a.skills) or 'general'}."
            for a in self.all()
        )

    def export(self) -> str:
        return json.dumps([a.model_dump() for a in self.all()], indent=2)
