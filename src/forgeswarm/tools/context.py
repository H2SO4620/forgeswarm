"""Shared context tools: the swarm's blackboard, decision log, and briefings."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..models import Briefing, ContextEntry, Decision
from ..store import Store


def register(mcp: FastMCP, store: Store) -> None:
    @mcp.tool()
    def save_context(
        project_id: int,
        key: str,
        content: str,
        tags: Optional[list[str]] = None,
        agent_id: str = "",
    ) -> ContextEntry:
        """Write a fact to the shared blackboard so every agent can find it.

        Use stable, descriptive keys ('api-schema', 'auth-flow'); writing the
        same key again updates it. Tag entries with 'task-<id>' to make them
        show up in that task's briefing.
        """
        return store.save_context(project_id, key=key, content=content, tags=tags, author=agent_id)

    @mcp.tool()
    def search_context(project_id: int, query: str = "", limit: int = 20) -> list[ContextEntry]:
        """Search the shared blackboard by substring over keys, content, and tags.

        An empty query returns the most recently updated entries.
        """
        return store.search_context(project_id, query=query, limit=limit)

    @mcp.tool()
    def record_decision(
        project_id: int, decision: str, rationale: str = "", agent_id: str = ""
    ) -> Decision:
        """Log an architectural/engineering decision so the swarm stops re-litigating it.

        Decisions appear in every briefing. Record the WHY in `rationale` —
        future agents only see what is written here.
        """
        return store.record_decision(project_id, decision=decision, rationale=rationale, author=agent_id)

    @mcp.tool()
    def get_briefing(task_id: int) -> Briefing:
        """Get an onboarding packet for a task: everything needed to start cold.

        Bundles the project goal and constraints, the task itself, summaries of
        its completed dependencies, all recorded decisions, related shared
        context, and reviewer feedback from earlier iterations. Call this right
        after claim_task, and again after a review requests changes.
        """
        return store.get_briefing(task_id)
