"""Planning tools: turn an engineering goal into a project with a task graph."""

from __future__ import annotations


from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from ..models import Project, Task
from ..store import Store, StoreError


class PlannedTask(BaseModel):
    """One task in a submitted plan."""

    title: str
    description: str = ""
    role: str = Field(default="any", description="Suggested role: planner/implementer/reviewer/any.")
    priority: int = Field(default=2, ge=1, le=3, description="1 = highest, 3 = lowest.")
    depends_on: list[int] = Field(
        default_factory=list,
        description="Zero-based indexes of earlier tasks IN THIS PLAN that must finish first.",
    )


def register(mcp: FastMCP, store: Store) -> None:
    @mcp.tool()
    def create_project(goal: str, constraints: str = "") -> Project:
        """Register a new engineering goal and get a project id.

        Call this once per goal, then decompose it with submit_plan.
        `constraints` holds non-negotiables (stack, deadline, style rules)
        that every agent should respect.
        """
        return store.create_project(goal=goal, constraints=constraints)

    @mcp.tool()
    def submit_plan(project_id: int, tasks: list[PlannedTask]) -> dict:
        """Decompose a project goal into a dependency-ordered task graph in one call.

        `depends_on` entries are zero-based indexes into the `tasks` list itself
        (task 2 depending on [0, 1] waits for the first two tasks). Returns the
        created tasks with their real ids; agents then claim ready tasks with
        claim_task.
        """
        created: list[Task] = []
        for i, t in enumerate(tasks):
            deps = []
            for idx in t.depends_on:
                if not 0 <= idx < i:
                    raise StoreError(
                        f"Task #{i} ('{t.title}') depends on index {idx}, which must refer"
                        " to an EARLIER task in this plan (0-based)."
                    )
                deps.append(created[idx].id)
            created.append(
                store.create_task(
                    project_id=project_id,
                    title=t.title,
                    description=t.description,
                    role=t.role,
                    priority=t.priority,
                    depends_on=deps,
                )
            )
        return {
            "project_id": project_id,
            "created_tasks": [{"id": t.id, "title": t.title, "depends_on": t.depends_on} for t in created],
        }

    @mcp.tool()
    def list_projects() -> list[Project]:
        """List all projects in this swarm with their status."""
        return store.list_projects()

    @mcp.tool()
    def register_agent(agent_id: str, role: str = "any", capabilities: str = "") -> dict:
        """Introduce yourself to the swarm. Call once at startup.

        Pick a stable, unique agent_id (e.g. 'impl-1', 'reviewer-claude') and
        use the same id in every later call: claims, submissions and reviews
        are attributed to it, and self-review is rejected based on it.
        """
        agent = store.register_agent(agent_id, role=role, capabilities=capabilities)
        others = [a.model_dump() for a in store.list_agents() if a.id != agent_id]
        return {"registered": agent.model_dump(), "other_agents": others}
