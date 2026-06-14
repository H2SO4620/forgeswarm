"""SQLite persistence layer for ForgeSwarm.

State lives on disk (WAL mode) so that:
- multiple agents connecting over stdio (each spawns its own server process)
  still share one swarm state;
- long-running engineering work survives crashes and restarts;
- task claiming is atomic (a single conditional UPDATE), so two agents can
  never claim the same task.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .models import (
    Agent,
    Briefing,
    CheckRun,
    ContextEntry,
    Decision,
    Discussion,
    DiscussionPost,
    Project,
    Review,
    Submission,
    Task,
)

DEFAULT_LEASE_SECONDS = 900

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    constraints TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'any',
    priority INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL DEFAULT 'open',
    claimed_by TEXT,
    lease_expires_at TEXT,
    iteration INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    artifacts TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_deps (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    depends_on INTEGER NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on)
);
CREATE TABLE IF NOT EXISTS context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    key TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    author TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, key)
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    content TEXT NOT NULL,
    self_assessment TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    submitted_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    verdict TEXT NOT NULL,
    comments TEXT NOT NULL DEFAULT '',
    reviewer TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    command TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    duration_seconds REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discussions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    topic TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    opened_by TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS discussion_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id INTEGER NOT NULL REFERENCES discussions(id),
    agent_id TEXT NOT NULL,
    position TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'any',
    capabilities TEXT NOT NULL DEFAULT '',
    last_seen TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_db_path() -> Path:
    env = os.environ.get("FORGESWARM_DB")
    if env:
        return Path(env)
    return Path.home() / ".forgeswarm" / "forgeswarm.db"


class StoreError(Exception):
    """Raised for invalid operations; message is safe to surface to agents."""


class Store:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------- projects

    def create_project(self, goal: str, constraints: str = "") -> Project:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO projects (goal, constraints, created_at) VALUES (?, ?, ?)",
                (goal, constraints, _now()),
            )
        return self.get_project(cur.lastrowid)

    def get_project(self, project_id: int) -> Project:
        row = self._conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise StoreError(f"No project with id {project_id}.")
        return Project(**dict(row))

    def list_projects(self) -> list[Project]:
        rows = self._conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
        return [Project(**dict(r)) for r in rows]

    def set_project_status(self, project_id: int, status: str) -> Project:
        self.get_project(project_id)
        with self._lock, self._conn:
            self._conn.execute("UPDATE projects SET status=? WHERE id=?", (status, project_id))
        return self.get_project(project_id)

    # ---------------------------------------------------------------- tasks

    def _task_from_row(self, row: sqlite3.Row) -> Task:
        deps = [
            r["depends_on"]
            for r in self._conn.execute(
                "SELECT depends_on FROM task_deps WHERE task_id=? ORDER BY depends_on", (row["id"],)
            )
        ]
        d = dict(row)
        d["artifacts"] = json.loads(d["artifacts"])
        d["depends_on"] = deps
        return Task(**d)

    def create_task(
        self,
        project_id: int,
        title: str,
        description: str = "",
        role: str = "any",
        priority: int = 2,
        depends_on: Optional[list[int]] = None,
    ) -> Task:
        self.get_project(project_id)
        depends_on = depends_on or []
        for dep in depends_on:
            self.get_task(dep)
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO tasks (project_id, title, description, role, priority,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, title, description, role, priority, now, now),
            )
            task_id = cur.lastrowid
            for dep in depends_on:
                self._conn.execute(
                    "INSERT OR IGNORE INTO task_deps (task_id, depends_on) VALUES (?, ?)",
                    (task_id, dep),
                )
        return self.get_task(task_id)

    def get_task(self, task_id: int) -> Task:
        row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise StoreError(f"No task with id {task_id}.")
        return self._task_from_row(row)

    def _release_expired_leases(self) -> None:
        """Tasks whose claimant stopped renewing its lease go back to open."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status='open', claimed_by=NULL, lease_expires_at=NULL,"
                " updated_at=? WHERE status IN ('claimed', 'in_progress')"
                " AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (_now(), _now()),
            )

    def _deps_done(self, task_id: int) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM task_deps d JOIN tasks t ON t.id = d.depends_on"
            " WHERE d.task_id=? AND t.status != 'done'",
            (task_id,),
        ).fetchone()
        return row["n"] == 0

    def list_tasks(
        self,
        project_id: int,
        status: Optional[str] = None,
        ready_only: bool = False,
    ) -> list[Task]:
        self._release_expired_leases()
        q = "SELECT * FROM tasks WHERE project_id=?"
        args: list[Any] = [project_id]
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY priority, id"
        tasks = [self._task_from_row(r) for r in self._conn.execute(q, args)]
        if ready_only:
            tasks = [t for t in tasks if t.status == "open" and self._deps_done(t.id)]
        return tasks

    def claim_task(
        self, task_id: int, agent_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS
    ) -> Task:
        self._release_expired_leases()
        task = self.get_task(task_id)
        if task.status != "open":
            raise StoreError(
                f"Task {task_id} is '{task.status}'"
                + (f" (claimed by {task.claimed_by})." if task.claimed_by else ".")
            )
        if not self._deps_done(task_id):
            pending = [d for d in task.depends_on if self.get_task(d).status != "done"]
            raise StoreError(f"Task {task_id} is not ready: dependencies {pending} are not done.")
        lease = (
            datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        ).isoformat(timespec="seconds")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET status='claimed', claimed_by=?, lease_expires_at=?,"
                " updated_at=? WHERE id=? AND status='open'",
                (agent_id, lease, _now(), task_id),
            )
        if cur.rowcount != 1:  # another agent won the race between our check and the update
            raise StoreError(f"Task {task_id} was claimed by another agent first.")
        self.touch_agent(agent_id)
        return self.get_task(task_id)

    def release_task(
        self, task_id: int, agent_id: str, reason: str = ""
    ) -> Task:
        task = self.get_task(task_id)
        if task.claimed_by and task.claimed_by != agent_id:
            raise StoreError(f"Task {task_id} is claimed by {task.claimed_by}, not {agent_id}.")
        if task.status not in ("claimed", "in_progress"):
            raise StoreError(
                f"Task {task_id} is '{task.status}';"
                " only claimed/in_progress tasks can be released."
            )
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE tasks SET status='open', claimed_by=NULL, lease_expires_at=NULL,"
                " updated_at=? WHERE id=? AND claimed_by=?"
                " AND status IN ('claimed', 'in_progress')",
                (_now(), task_id, agent_id),
            )
        if cur.rowcount != 1:
            raise StoreError(f"Task {task_id} state changed during release; try again.")
        if reason.strip():
            self.save_context(
                task.project_id,
                key=f"task-{task_id}-release-{_now()}",
                content=reason.strip(),
                tags=[f"task-{task_id}"],
                author=agent_id,
            )
        self.touch_agent(agent_id)
        return self.get_task(task_id)

    def update_task(
        self,
        task_id: int,
        agent_id: str,
        status: Optional[str] = None,
        notes: str = "",
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> Task:
        task = self.get_task(task_id)
        if task.claimed_by and task.claimed_by != agent_id:
            raise StoreError(f"Task {task_id} is claimed by {task.claimed_by}, not {agent_id}.")
        if status is not None and status not in ("claimed", "in_progress"):
            raise StoreError(
                "update_task only moves between 'claimed' and 'in_progress'."
                " Use submit_for_review or complete_task to finish a task."
            )
        lease = (
            datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        ).isoformat(timespec="seconds")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status=COALESCE(?, status), lease_expires_at=?, updated_at=?"
                " WHERE id=?",
                (status, lease, _now(), task_id),
            )
        if notes:
            self.save_context(
                task.project_id,
                key=f"task-{task_id}-notes-{_now()}",
                content=notes,
                tags=[f"task-{task_id}", "progress-note"],
                author=agent_id,
            )
        self.touch_agent(agent_id)
        return self.get_task(task_id)

    def complete_task(
        self,
        task_id: int,
        agent_id: str,
        summary: str,
        artifacts: Optional[list[str]] = None,
    ) -> Task:
        task = self.get_task(task_id)
        if task.status == "done":
            raise StoreError(f"Task {task_id} is already done.")
        if task.claimed_by and task.claimed_by != agent_id:
            raise StoreError(f"Task {task_id} is claimed by {task.claimed_by}, not {agent_id}.")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status='done', summary=?, artifacts=?, lease_expires_at=NULL,"
                " updated_at=? WHERE id=?",
                (summary, json.dumps(artifacts or []), _now(), task_id),
            )
        self.touch_agent(agent_id)
        return self.get_task(task_id)

    def task_graph(self, project_id: int) -> dict:
        tasks = self.list_tasks(project_id)
        return {
            "project_id": project_id,
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "depends_on": t.depends_on,
                    "claimed_by": t.claimed_by,
                    "ready": t.status == "open" and self._deps_done(t.id),
                }
                for t in tasks
            ],
        }

    # -------------------------------------------------------------- context

    def save_context(
        self, project_id: int, key: str, content: str, tags: Optional[list[str]] = None,
        author: str = "",
    ) -> ContextEntry:
        self.get_project(project_id)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO context (project_id, key, content, tags, author, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (project_id, key) DO UPDATE SET content=excluded.content,"
                " tags=excluded.tags, author=excluded.author, updated_at=excluded.updated_at",
                (project_id, key, content, json.dumps(tags or []), author, _now()),
            )
        row = self._conn.execute(
            "SELECT * FROM context WHERE project_id=? AND key=?", (project_id, key)
        ).fetchone()
        return self._context_from_row(row)

    @staticmethod
    def _context_from_row(row: sqlite3.Row) -> ContextEntry:
        d = dict(row)
        d["tags"] = json.loads(d["tags"])
        return ContextEntry(**d)

    def search_context(self, project_id: int, query: str = "", limit: int = 20) -> list[ContextEntry]:
        like = f"%{query}%"
        rows = self._conn.execute(
            "SELECT * FROM context WHERE project_id=? AND (key LIKE ? OR content LIKE ?"
            " OR tags LIKE ?) ORDER BY updated_at DESC LIMIT ?",
            (project_id, like, like, like, limit),
        ).fetchall()
        return [self._context_from_row(r) for r in rows]

    def record_decision(
        self, project_id: int, decision: str, rationale: str = "", author: str = ""
    ) -> Decision:
        self.get_project(project_id)
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO decisions (project_id, decision, rationale, author, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (project_id, decision, rationale, author, _now()),
            )
        row = self._conn.execute("SELECT * FROM decisions WHERE id=?", (cur.lastrowid,)).fetchone()
        return Decision(**dict(row))

    def list_decisions(self, project_id: int) -> list[Decision]:
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE project_id=? ORDER BY id", (project_id,)
        ).fetchall()
        return [Decision(**dict(r)) for r in rows]

    # --------------------------------------------------------------- review

    def submit_for_review(
        self, task_id: int, agent_id: str, content: str, self_assessment: str = ""
    ) -> Submission:
        task = self.get_task(task_id)
        if task.status not in ("claimed", "in_progress"):
            raise StoreError(
                f"Task {task_id} is '{task.status}'; only claimed/in_progress tasks can be"
                " submitted for review."
            )
        if task.claimed_by and task.claimed_by != agent_id:
            raise StoreError(f"Task {task_id} is claimed by {task.claimed_by}, not {agent_id}.")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO submissions (task_id, content, self_assessment, submitted_by,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, content, self_assessment, agent_id, _now()),
            )
            self._conn.execute(
                "UPDATE tasks SET status='in_review', lease_expires_at=NULL, updated_at=?"
                " WHERE id=?",
                (_now(), task_id),
            )
        self.touch_agent(agent_id)
        return self.get_submission(cur.lastrowid)

    def get_submission(self, submission_id: int) -> Submission:
        row = self._conn.execute(
            "SELECT * FROM submissions WHERE id=?", (submission_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"No submission with id {submission_id}.")
        return Submission(**dict(row))

    def review_queue(self, project_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT s.*, t.title, t.project_id, t.iteration FROM submissions s"
            " JOIN tasks t ON t.id = s.task_id"
            " WHERE s.status='pending' AND t.project_id=? ORDER BY s.id",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def post_review(
        self, submission_id: int, reviewer: str, verdict: str, comments: str = ""
    ) -> Review:
        sub = self.get_submission(submission_id)
        if sub.status != "pending":
            raise StoreError(f"Submission {submission_id} was already reviewed ({sub.status}).")
        if verdict not in ("approve", "request_changes"):
            raise StoreError("verdict must be 'approve' or 'request_changes'.")
        if reviewer == sub.submitted_by:
            raise StoreError("Self-review is not allowed: a different agent must review.")
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO reviews (submission_id, task_id, verdict, comments, reviewer,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (submission_id, sub.task_id, verdict, comments, reviewer, now),
            )
            if verdict == "approve":
                self._conn.execute(
                    "UPDATE submissions SET status='approved' WHERE id=?", (submission_id,)
                )
                self._conn.execute(
                    "UPDATE tasks SET status='done', summary=?, updated_at=? WHERE id=?",
                    (sub.content[:500], now, sub.task_id),
                )
            else:
                # The loop is enforced here: the task returns to its claimant
                # with the feedback attached, and the iteration counter ticks.
                lease = (
                    datetime.now(timezone.utc) + timedelta(seconds=DEFAULT_LEASE_SECONDS)
                ).isoformat(timespec="seconds")
                self._conn.execute(
                    "UPDATE submissions SET status='changes_requested' WHERE id=?",
                    (submission_id,),
                )
                self._conn.execute(
                    "UPDATE tasks SET status='in_progress', iteration=iteration+1,"
                    " claimed_by=?, lease_expires_at=?, updated_at=? WHERE id=?",
                    (sub.submitted_by, lease, now, sub.task_id),
                )
        self.touch_agent(reviewer)
        row = self._conn.execute("SELECT * FROM reviews WHERE id=?", (cur.lastrowid,)).fetchone()
        return Review(**dict(row))

    def reviews_for_task(self, task_id: int) -> list[Review]:
        rows = self._conn.execute(
            "SELECT * FROM reviews WHERE task_id=? ORDER BY id", (task_id,)
        ).fetchall()
        return [Review(**dict(r)) for r in rows]

    # --------------------------------------------------------------- checks

    def record_check(
        self, task_id: int, command: str, exit_code: int, stdout: str, stderr: str,
        duration_seconds: float,
    ) -> CheckRun:
        self.get_task(task_id)
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO checks (task_id, command, exit_code, stdout, stderr,"
                " duration_seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, command, exit_code, stdout, stderr, duration_seconds, _now()),
            )
        row = self._conn.execute("SELECT * FROM checks WHERE id=?", (cur.lastrowid,)).fetchone()
        return CheckRun(**dict(row))

    # --------------------------------------------------------------- agents

    def register_agent(self, agent_id: str, role: str = "any", capabilities: str = "") -> Agent:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO agents (id, role, capabilities, last_seen) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET role=excluded.role,"
                " capabilities=excluded.capabilities, last_seen=excluded.last_seen",
                (agent_id, role, capabilities, _now()),
            )
        row = self._conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        return Agent(**dict(row))

    def touch_agent(self, agent_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO agents (id, last_seen) VALUES (?, ?)"
                " ON CONFLICT (id) DO UPDATE SET last_seen=excluded.last_seen",
                (agent_id, _now()),
            )

    def list_agents(self) -> list[Agent]:
        rows = self._conn.execute("SELECT * FROM agents ORDER BY id").fetchall()
        return [Agent(**dict(r)) for r in rows]

    # ---------------------------------------------------------- discussions

    def open_discussion(self, project_id: int, topic: str, agent_id: str = "") -> Discussion:
        self.get_project(project_id)
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO discussions (project_id, topic, opened_by, created_at)"
                " VALUES (?, ?, ?, ?)",
                (project_id, topic, agent_id, _now()),
            )
        if agent_id:
            self.touch_agent(agent_id)
        return self.get_discussion(cur.lastrowid)

    def get_discussion(self, discussion_id: int) -> Discussion:
        row = self._conn.execute(
            "SELECT * FROM discussions WHERE id=?", (discussion_id,)
        ).fetchone()
        if row is None:
            raise StoreError(f"No discussion with id {discussion_id}.")
        return Discussion(**dict(row))

    def list_discussions(self, project_id: int, status: Optional[str] = None) -> list[Discussion]:
        q = "SELECT * FROM discussions WHERE project_id=?"
        args: list[Any] = [project_id]
        if status:
            q += " AND status=?"
            args.append(status)
        return [Discussion(**dict(r)) for r in self._conn.execute(q + " ORDER BY id", args)]

    def discussion_posts(self, discussion_id: int) -> list[DiscussionPost]:
        rows = self._conn.execute(
            "SELECT * FROM discussion_posts WHERE discussion_id=? ORDER BY id",
            (discussion_id,),
        ).fetchall()
        return [DiscussionPost(**dict(r)) for r in rows]

    def post_to_discussion(
        self, discussion_id: int, agent_id: str, position: str
    ) -> DiscussionPost:
        disc = self.get_discussion(discussion_id)
        if disc.status != "open":
            raise StoreError(
                f"Discussion {discussion_id} is resolved: {disc.resolution!r}."
                " Open a new discussion to revisit it."
            )
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO discussion_posts (discussion_id, agent_id, position, created_at)"
                " VALUES (?, ?, ?, ?)",
                (discussion_id, agent_id, position, _now()),
            )
        self.touch_agent(agent_id)
        row = self._conn.execute(
            "SELECT * FROM discussion_posts WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return DiscussionPost(**dict(row))

    def resolve_discussion(
        self, discussion_id: int, agent_id: str, resolution: str, rationale: str = ""
    ) -> dict:
        disc = self.get_discussion(discussion_id)
        if disc.status != "open":
            raise StoreError(f"Discussion {discussion_id} is already resolved.")
        posts = self.discussion_posts(discussion_id)
        voices = {p.agent_id for p in posts}
        # consensus needs an actual exchange, not a monologue
        if len(voices) < 2:
            raise StoreError(
                f"Discussion {discussion_id} has positions from {len(voices)} agent(s);"
                " at least 2 distinct agents must post before it can be resolved."
            )
        digest = "; ".join(f"{p.agent_id}: {p.position[:120]}" for p in posts[-6:])
        decision = self.record_decision(
            disc.project_id,
            decision=resolution,
            rationale=(rationale + " " if rationale else "")
            + f"[Consensus from discussion #{discussion_id} '{disc.topic}' — {digest}]",
            author=agent_id,
        )
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE discussions SET status='resolved', resolution=?, resolved_at=?"
                " WHERE id=?",
                (resolution, _now(), discussion_id),
            )
        return {
            "discussion": self.get_discussion(discussion_id).model_dump(),
            "recorded_decision": decision.model_dump(),
        }

    # ---------------------------------------------------------------- retro

    def get_retrospective(self, project_id: int) -> dict:
        project = self.get_project(project_id)
        tasks = self.list_tasks(project_id)
        reviews = self._conn.execute(
            "SELECT r.* FROM reviews r JOIN tasks t ON t.id = r.task_id WHERE t.project_id=?",
            (project_id,),
        ).fetchall()
        checks = self._conn.execute(
            "SELECT c.* FROM checks c JOIN tasks t ON t.id = c.task_id WHERE t.project_id=?",
            (project_id,),
        ).fetchall()
        subs = self._conn.execute(
            "SELECT s.* FROM submissions s JOIN tasks t ON t.id = s.task_id"
            " WHERE t.project_id=?",
            (project_id,),
        ).fetchall()

        bounces = sum(1 for r in reviews if r["verdict"] == "request_changes")
        checks_passed = sum(1 for c in checks if c["exit_code"] == 0)

        agents: dict[str, dict] = {}

        def agent(aid: str) -> dict:
            return agents.setdefault(
                aid,
                {"agent_id": aid, "tasks_completed": 0, "submissions": 0,
                 "changes_requested_received": 0, "reviews_given": 0},
            )

        for t in tasks:
            if t.status == "done" and t.claimed_by:
                agent(t.claimed_by)["tasks_completed"] += 1
        for s in subs:
            agent(s["submitted_by"])["submissions"] += 1
            if s["status"] == "changes_requested":
                agent(s["submitted_by"])["changes_requested_received"] += 1
        for r in reviews:
            agent(r["reviewer"])["reviews_given"] += 1

        hotspots = sorted(
            ({"id": t.id, "title": t.title, "iterations": t.iteration}
             for t in tasks if t.iteration > 0),
            key=lambda x: -x["iterations"],
        )
        return {
            "project": project.model_dump(),
            "totals": {
                "tasks": len(tasks),
                "done": sum(1 for t in tasks if t.status == "done"),
                "total_iterations": sum(t.iteration for t in tasks),
                "reviews": len(reviews),
                "review_bounce_rate": round(bounces / len(reviews), 2) if reviews else None,
                "check_runs": len(checks),
                "check_pass_rate": round(checks_passed / len(checks), 2) if checks else None,
                "decisions_recorded": len(self.list_decisions(project_id)),
            },
            "agents": sorted(agents.values(), key=lambda a: a["agent_id"]),
            "hotspots": hotspots,
        }

    # ------------------------------------------------------------- briefing

    def get_briefing(self, task_id: int) -> Briefing:
        task = self.get_task(task_id)
        project = self.get_project(task.project_id)
        dep_summaries = [
            {"id": d.id, "title": d.title, "summary": d.summary}
            for d in (self.get_task(dep) for dep in task.depends_on)
            if d.status == "done"
        ]
        related = self.search_context(task.project_id, f"task-{task_id}", limit=10)
        # also surface the most recent general context entries
        seen = {c.id for c in related}
        for entry in self.search_context(task.project_id, "", limit=5):
            if entry.id not in seen:
                related.append(entry)
        return Briefing(
            project=project,
            task=task,
            dependency_summaries=dep_summaries,
            decisions=self.list_decisions(task.project_id),
            related_context=related,
            prior_review_feedback=self.reviews_for_task(task_id),
        )
