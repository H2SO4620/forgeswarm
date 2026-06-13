"""ForgeSwarm MCP server entrypoint.

Builds the FastMCP app, wires the SQLite store into every tool/resource/
prompt group, and runs over stdio (default) or streamable HTTP.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from . import prompts, resources
from .store import Store
from .tools import checks, context, discussion, planning, retro, review, tasks, templates

INSTRUCTIONS = """ForgeSwarm coordinates multiple AI agents doing software engineering work.

Typical flow:
1. register_agent (pick a stable agent_id, use it everywhere).
2. A planner calls create_project + submit_plan to build the task graph.
3. Workers loop: list_tasks(ready_only=true) -> claim_task -> get_briefing ->
   do the work (save_context / record_decision / run_checks) -> submit_for_review.
4. A different agent reviews via get_review_queue + post_review; 'request_changes'
   sends the task back to its author automatically with the feedback attached.
5. Disagreements go through open_discussion / post_to_discussion /
   resolve_discussion — the resolution becomes a binding recorded decision.
6. get_task_graph / get_retrospective / the swarm:// resources show live
   project state and swarm performance at any time.
"""


def create_server(
    db_path: Optional[Path] = None, host: str = "127.0.0.1", port: int = 8765
) -> FastMCP:
    store = Store(db_path)
    mcp = FastMCP("ForgeSwarm", instructions=INSTRUCTIONS, host=host, port=port)
    planning.register(mcp, store)
    tasks.register(mcp, store)
    context.register(mcp, store)
    review.register(mcp, store)
    checks.register(mcp, store)
    discussion.register(mcp, store)
    templates.register(mcp, store)
    retro.register(mcp, store)
    resources.register(mcp, store)
    prompts.register(mcp, store)
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="forgeswarm",
        description="ForgeSwarm MCP server: multi-agent engineering orchestration.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio for local clients (default), http for a shared swarm endpoint.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=8765, help="HTTP bind port.")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite db path (default: ~/.forgeswarm/forgeswarm.db or $FORGESWARM_DB).",
    )
    args = parser.parse_args()

    mcp = create_server(db_path=args.db, host=args.host, port=args.port)
    if args.transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
