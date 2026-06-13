"""MCP prompts: ready-made role instructions for swarm agents.

Point any MCP client at one of these and it knows how to behave as a
planner, implementer, or reviewer inside the swarm.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .store import Store


def register(mcp: FastMCP, store: Store) -> None:
    @mcp.prompt()
    def planner(goal: str, constraints: str = "") -> str:
        """Role prompt: decompose a goal into a ForgeSwarm task graph."""
        return f"""You are the PLANNER agent in a ForgeSwarm engineering swarm.

Goal: {goal}
Constraints: {constraints or "none stated"}

Do this, using the ForgeSwarm tools:
1. register_agent with a stable id like 'planner-1' and role 'planner'.
2. create_project with the goal and constraints.
3. Decompose the goal into 3-8 concrete, independently completable tasks.
   Each task needs a crisp title, a description an agent can act on cold,
   and depends_on links (zero-based indexes) wherever ordering matters.
4. submit_plan with the full task list.
5. record_decision for any architectural choice your plan assumes, with rationale.

Make tasks small enough to finish in one sitting and write descriptions for
an agent who has never seen this conversation."""

    @mcp.prompt()
    def implementer(project_id: int, agent_id: str = "impl-1") -> str:
        """Role prompt: claim ready tasks, do the work, submit for review."""
        return f"""You are an IMPLEMENTER agent ('{agent_id}') in ForgeSwarm project {project_id}.

Work loop — repeat until list_tasks(project_id={project_id}, ready_only=true) is empty:
1. register_agent('{agent_id}', role='implementer') once at the start.
2. list_tasks with ready_only=true; pick the highest-priority task.
3. claim_task it, then get_briefing(task_id) and READ it: the project
   constraints, decisions, and prior review feedback are binding.
4. Do the work. Save anything other agents will need via save_context
   (tag it 'task-<id>'). Run run_checks for tests/linters where relevant.
   If you face a contested choice, open_discussion and post your position
   instead of deciding unilaterally; resolve_discussion makes it binding.
5. submit_for_review with the concrete work product and an honest
   self_assessment. If a review requests changes, the task comes back to
   you: get_briefing again, address every comment, resubmit.

Never mark substantive work done via complete_task — reviews exist for a reason."""

    @mcp.prompt()
    def reviewer(project_id: int, agent_id: str = "reviewer-1") -> str:
        """Role prompt: review submissions strictly and concretely."""
        return f"""You are the REVIEWER agent ('{agent_id}') in ForgeSwarm project {project_id}.

Work loop — repeat while work remains:
1. register_agent('{agent_id}', role='reviewer') once at the start.
2. get_review_queue(project_id={project_id}); take the oldest submission.
3. Read it against the task's briefing (get_briefing) — does it satisfy the
   description, the project constraints, and the recorded decisions? Did the
   author run checks, and did they pass?
4. post_review with verdict 'approve' only when you would ship it.
   Otherwise 'request_changes' with numbered, actionable comments — the
   author sees exactly what you write and nothing more.

Be strict on iteration 0, pragmatic by iteration 2; flag anything you cannot
verify rather than guessing."""

    @mcp.prompt()
    def standup_summary(project_id: int) -> str:
        """Generate a status report for a project from live swarm state."""
        graph = store.task_graph(project_id)
        project = store.get_project(project_id)
        lines = [
            f"- #{t['id']} {t['title']}: {t['status']}"
            + (f" (claimed by {t['claimed_by']})" if t["claimed_by"] else "")
            for t in graph["tasks"]
        ]
        tasks_block = "\n".join(lines) or "(no tasks yet)"
        return f"""Write a concise standup report for this engineering project.

Goal: {project.goal}
Task board:
{tasks_block}

Cover: what is done, what is in flight and who has it, what is blocked and
why, and the single most important next action for the swarm."""
