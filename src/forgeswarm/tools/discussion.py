"""Discussion tools: structured debate that ends in durable, binding memory.

Consensus is server-enforced: a discussion cannot be resolved until at least
two distinct agents have posted positions, and resolving it automatically
records a decision — which then appears in every future briefing on the
project. Debate once, remember forever.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..models import Discussion, DiscussionPost
from ..store import Store


def register(mcp: FastMCP, store: Store) -> None:
    @mcp.tool()
    def open_discussion(project_id: int, topic: str, agent_id: str = "") -> Discussion:
        """Open a discussion when agents disagree or a choice needs multiple perspectives.

        Frame the topic as a decidable question ('Postgres or SQLite for the
        cache?'), then invite positions via post_to_discussion.
        """
        return store.open_discussion(project_id, topic=topic, agent_id=agent_id)

    @mcp.tool()
    def post_to_discussion(discussion_id: int, agent_id: str, position: str) -> DiscussionPost:
        """State your position in an open discussion, with reasoning.

        Read the existing posts first (list_discussions / get_briefing show
        them) and respond to the strongest opposing argument, not past it.
        """
        return store.post_to_discussion(discussion_id, agent_id=agent_id, position=position)

    @mcp.tool()
    def resolve_discussion(
        discussion_id: int, agent_id: str, resolution: str, rationale: str = ""
    ) -> dict:
        """Close a discussion with the consensus and make it binding project memory.

        Requires positions from at least 2 distinct agents. The resolution is
        automatically recorded as a project decision (with a digest of the
        debate as rationale), so every future briefing carries it — the swarm
        will not re-litigate.
        """
        return store.resolve_discussion(
            discussion_id, agent_id=agent_id, resolution=resolution, rationale=rationale
        )

    @mcp.tool()
    def list_discussions(
        project_id: int, status: Optional[str] = None, include_posts: bool = True
    ) -> list[dict]:
        """List discussions on a project ('open' or 'resolved'), with their posts."""
        result = []
        for disc in store.list_discussions(project_id, status=status):
            d = disc.model_dump()
            if include_posts:
                d["posts"] = [p.model_dump() for p in store.discussion_posts(disc.id)]
            result.append(d)
        return result
