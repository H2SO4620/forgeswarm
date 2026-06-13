"""Pydantic models for ForgeSwarm entities.

These are the shapes returned by tools and resources. Keeping them as
models (rather than raw dicts) gives agents a stable, documented contract.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TaskStatus = Literal["open", "claimed", "in_progress", "in_review", "done"]
Verdict = Literal["approve", "request_changes"]


class Project(BaseModel):
    id: int
    goal: str
    constraints: str = ""
    status: Literal["active", "done", "archived"] = "active"
    created_at: str


class Task(BaseModel):
    id: int
    project_id: int
    title: str
    description: str = ""
    role: str = Field(default="any", description="Suggested agent role, e.g. 'implementer'.")
    priority: int = Field(default=2, description="1 = highest, 3 = lowest.")
    status: TaskStatus = "open"
    depends_on: list[int] = Field(default_factory=list)
    claimed_by: Optional[str] = None
    lease_expires_at: Optional[str] = None
    iteration: int = Field(default=0, description="How many review round-trips this task has had.")
    summary: str = Field(default="", description="Completion summary written by the finishing agent.")
    artifacts: list[str] = Field(default_factory=list, description="Paths/URLs produced by the task.")
    created_at: str
    updated_at: str


class ContextEntry(BaseModel):
    id: int
    project_id: int
    key: str
    content: str
    tags: list[str] = Field(default_factory=list)
    author: str = ""
    updated_at: str


class Decision(BaseModel):
    id: int
    project_id: int
    decision: str
    rationale: str = ""
    author: str = ""
    created_at: str


class Submission(BaseModel):
    id: int
    task_id: int
    content: str = Field(description="The work product under review: a diff, file list, or summary.")
    self_assessment: str = ""
    status: Literal["pending", "approved", "changes_requested"] = "pending"
    submitted_by: str = ""
    created_at: str


class Review(BaseModel):
    id: int
    submission_id: int
    task_id: int
    verdict: Verdict
    comments: str = ""
    reviewer: str = ""
    created_at: str


class CheckRun(BaseModel):
    id: int
    task_id: int
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    created_at: str


class Discussion(BaseModel):
    id: int
    project_id: int
    topic: str
    status: Literal["open", "resolved"] = "open"
    opened_by: str = ""
    resolution: str = ""
    created_at: str
    resolved_at: Optional[str] = None


class DiscussionPost(BaseModel):
    id: int
    discussion_id: int
    agent_id: str
    position: str
    created_at: str


class Agent(BaseModel):
    id: str
    role: str = "any"
    capabilities: str = ""
    last_seen: str


class Briefing(BaseModel):
    """Everything an agent needs to start a task cold."""

    project: Project
    task: Task
    dependency_summaries: list[dict] = Field(
        default_factory=list, description="id/title/summary of completed dependency tasks."
    )
    decisions: list[Decision] = Field(default_factory=list)
    related_context: list[ContextEntry] = Field(default_factory=list)
    prior_review_feedback: list[Review] = Field(
        default_factory=list, description="Feedback from earlier iterations of this task."
    )
