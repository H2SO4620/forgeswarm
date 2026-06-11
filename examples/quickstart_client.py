"""ForgeSwarm quickstart: walk the full swarm workflow with a scripted client.

No LLM or API key needed — this spawns the server over stdio (exactly like an
MCP client would) and plays both an implementer and a reviewer, so you can see
every tool in action in ~5 seconds:

    python examples/quickstart_client.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(session: ClientSession, tool: str, **kwargs):
    result = await session.call_tool(tool, kwargs)
    if result.isError:
        raise RuntimeError(f"{tool} failed: {result.content[0].text}")
    sc = result.structuredContent
    if sc is not None:
        return sc["result"] if set(sc.keys()) == {"result"} else sc
    return json.loads(result.content[0].text)


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


async def main() -> None:
    db = Path(tempfile.mkdtemp()) / "quickstart.db"
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "forgeswarm"],
        env={**os.environ, "FORGESWARM_DB": str(db)},
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            step("Agents introduce themselves")
            await call(session, "register_agent", agent_id="impl-1", role="implementer")
            await call(session, "register_agent", agent_id="reviewer-1", role="reviewer")

            step("Planner: create project + submit the task graph in one call")
            project = await call(
                session, "create_project",
                goal="Build a CLI pomodoro timer",
                constraints="Python stdlib only",
            )
            plan = await call(session, "submit_plan", project_id=project["id"], tasks=[
                {"title": "Design the CLI interface", "role": "implementer"},
                {"title": "Implement the timer loop", "depends_on": [0]},
                {"title": "Write usage docs", "depends_on": [1]},
            ])
            ids = [t["id"] for t in plan["created_tasks"]]
            print("Task graph:", json.dumps(plan["created_tasks"], indent=2))

            step("Only dependency-free tasks are claimable")
            ready = await call(session, "list_tasks", project_id=project["id"], ready_only=True)
            print("Ready:", [(t["id"], t["title"]) for t in ready])

            step("impl-1 claims task 1; a second claim attempt is rejected")
            await call(session, "claim_task", task_id=ids[0], agent_id="impl-1")
            try:
                await call(session, "claim_task", task_id=ids[0], agent_id="impl-2")
            except RuntimeError as e:
                print("Atomic claim held:", e)

            step("impl-1 records a decision + shares context, then finishes task 1")
            await call(session, "record_decision", project_id=project["id"],
                       decision="Use argparse, no third-party CLI libs",
                       rationale="stdlib-only constraint", agent_id="impl-1")
            await call(session, "save_context", project_id=project["id"],
                       key="cli-design", content="pomo start [--minutes 25] / pomo stats",
                       tags=[f"task-{ids[0]}"], agent_id="impl-1")
            await call(session, "complete_task", task_id=ids[0], agent_id="impl-1",
                       summary="CLI design: pomo start [--minutes 25] / pomo stats")

            step("Task 2 unlocks; impl-1 gets a briefing (the cold-start packet)")
            await call(session, "claim_task", task_id=ids[1], agent_id="impl-1")
            briefing = await call(session, "get_briefing", task_id=ids[1])
            print("Briefing includes decision:", briefing["decisions"][0]["decision"])
            print("Briefing includes dependency summary:",
                  briefing["dependency_summaries"][0]["summary"])

            step("Review loop: submit -> request_changes -> resubmit -> approve")
            await call(session, "submit_for_review", task_id=ids[1], agent_id="impl-1",
                       content="timer loop v1 (no keyboard interrupt handling)",
                       self_assessment="happy path only")
            queue = await call(session, "get_review_queue", project_id=project["id"])
            await call(session, "post_review", submission_id=queue[0]["id"],
                       agent_id="reviewer-1", verdict="request_changes",
                       comments="1) handle Ctrl+C cleanly 2) show remaining time")
            task = await call(session, "get_briefing", task_id=ids[1])
            print("Bounced back to author with feedback:",
                  task["prior_review_feedback"][0]["comments"])

            await call(session, "submit_for_review", task_id=ids[1], agent_id="impl-1",
                       content="timer loop v2: SIGINT handled, countdown display")
            queue = await call(session, "get_review_queue", project_id=project["id"])
            await call(session, "post_review", submission_id=queue[0]["id"],
                       agent_id="reviewer-1", verdict="approve")

            step("Finish the docs task and check the board")
            await call(session, "claim_task", task_id=ids[2], agent_id="impl-1")
            await call(session, "complete_task", task_id=ids[2], agent_id="impl-1",
                       summary="README with usage examples")

            graph = await call(session, "get_task_graph", project_id=project["id"])
            print(json.dumps(graph, indent=2))

            status = await session.read_resource(f"swarm://project/{project['id']}/status")
            print("\nLive resource swarm://project/{id}/status:")
            print(status.contents[0].text)


if __name__ == "__main__":
    asyncio.run(main())
