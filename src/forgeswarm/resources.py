"""MCP resources: live swarm state, readable without tool calls.

Resources are the cheap, side-effect-free way for an agent (or a human in an
MCP inspector) to watch the swarm.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from .store import Store


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


def register(mcp: FastMCP, store: Store) -> None:
    @mcp.resource("swarm://projects")
    def all_projects() -> str:
        """All projects in this swarm."""
        return _dump([p.model_dump() for p in store.list_projects()])

    @mcp.resource("swarm://agents")
    def all_agents() -> str:
        """Registered agents and when they were last active."""
        return _dump([a.model_dump() for a in store.list_agents()])

    @mcp.resource("swarm://project/{project_id}/status")
    def project_status(project_id: int) -> str:
        """One-glance project health: goal, task counts by status, pending reviews."""
        project = store.get_project(project_id)
        tasks = store.list_tasks(project_id)
        counts: dict[str, int] = {}
        for t in tasks:
            counts[t.status] = counts.get(t.status, 0) + 1
        return _dump(
            {
                "project": project.model_dump(),
                "task_counts": counts,
                "total_tasks": len(tasks),
                "done": counts.get("done", 0) == len(tasks) and len(tasks) > 0,
                "pending_reviews": len(store.review_queue(project_id)),
            }
        )

    @mcp.resource("swarm://project/{project_id}/tasks")
    def project_tasks(project_id: int) -> str:
        """The full task graph for a project."""
        return _dump(store.task_graph(project_id))

    @mcp.resource("swarm://project/{project_id}/decisions")
    def project_decisions(project_id: int) -> str:
        """The project's decision log (ADR-style)."""
        return _dump([d.model_dump() for d in store.list_decisions(project_id)])

    @mcp.resource("swarm://project/{project_id}/discussions")
    def project_discussions(project_id: int) -> str:
        """Discussions on a project, including every posted position."""
        out = []
        for disc in store.list_discussions(project_id):
            d = disc.model_dump()
            d["posts"] = [p.model_dump() for p in store.discussion_posts(disc.id)]
            out.append(d)
        return _dump(out)

    @mcp.resource("swarm://project/{project_id}/retrospective")
    def project_retrospective(project_id: int) -> str:
        """Live swarm-performance stats: bounce rates, iterations, per-agent numbers."""
        return _dump(store.get_retrospective(project_id))

    @mcp.resource("swarm://project/{project_id}/context")
    def project_context(project_id: int) -> str:
        """Recent shared-context entries for a project."""
        return _dump([c.model_dump() for c in store.search_context(project_id, "", limit=50)])
