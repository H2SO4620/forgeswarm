"""Retrospective tool: hard evidence about how the swarm itself performed.

The server compiles the numbers (iterations, bounce rates, check pass rates,
per-agent stats); the agent reading them does the reflecting. Evidence from
the database, judgment from the model.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..store import Store


def register(mcp: FastMCP, store: Store) -> None:
    @mcp.tool()
    def get_retrospective(project_id: int) -> dict:
        """Compile the project's performance evidence for a swarm retrospective.

        Returns totals (review bounce rate, check pass rate, iterations),
        per-agent stats (tasks completed, submissions bounced, reviews given),
        and hotspots — the tasks that needed the most review round-trips.
        Analyze it to propose concrete process improvements, and record the
        ones the swarm adopts via record_decision.
        """
        return store.get_retrospective(project_id)
