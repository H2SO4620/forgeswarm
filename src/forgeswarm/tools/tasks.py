"""Task board tools: the coordination core of the swarm.

claim_task is atomic at the database level, so two agents polling the same
board can never end up working on the same task.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..models import Task
from ..store import DEFAULT_LEASE_SECONDS, Store


def register(mcp: FastMCP, store: Store) -> None:
    @mcp.tool()
    def list_tasks(
        project_id: int, status: Optional[str] = None, ready_only: bool = False
    ) -> list[Task]:
        """List tasks on the project board.

        Use ready_only=true to see only tasks you could claim right now
        (open, with all dependencies done). Status filter accepts:
        open, claimed, in_progress, in_review, done.
        """
        return store.list_tasks(project_id, status=status, ready_only=ready_only)

    @mcp.tool()
    def claim_task(
        task_id: int, agent_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS
    ) -> Task:
        """Atomically claim an open task. Fails if it is taken or not ready.

        The claim carries a lease: if you stop renewing it (update_task renews
        automatically) the task returns to the board so the swarm never stalls
        on a crashed agent. After claiming, call get_briefing before starting.
        """
        return store.claim_task(task_id, agent_id, lease_seconds=lease_seconds)

    @mcp.tool()
    def update_task(
        task_id: int,
        agent_id: str,
        status: Optional[str] = None,
        notes: str = "",
    ) -> Task:
        """Report progress on your claimed task and renew its lease.

        status may be 'claimed' or 'in_progress'. Any notes you pass are saved
        to shared context (tagged with the task id) so other agents can see
        what you are doing. Finishing goes through submit_for_review or
        complete_task instead.
        """
        return store.update_task(task_id, agent_id, status=status, notes=notes)

    @mcp.tool()
    def complete_task(
        task_id: int, agent_id: str, summary: str, artifacts: Optional[list[str]] = None
    ) -> Task:
        """Mark your claimed task done WITHOUT review (for trivial/mechanical tasks).

        For anything substantive, prefer submit_for_review so another agent
        checks the work. `summary` is what future briefings will show to agents
        whose tasks depend on this one — write it for them.
        """
        return store.complete_task(task_id, agent_id, summary=summary, artifacts=artifacts)

    @mcp.tool()
    def get_task_graph(project_id: int) -> dict:
        """See the whole dependency graph: every task, its status, blockers, and who holds it.

        Use this to find the critical path or to check whether the project is done.
        """
        return store.task_graph(project_id)
