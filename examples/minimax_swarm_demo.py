"""ForgeSwarm + MiniMax M3: a three-agent swarm coordinating over real MCP.

Three MiniMax M3 agents — planner, implementer, reviewer — connect to one
ForgeSwarm server (spawned over stdio) and build a small project together,
communicating ONLY through ForgeSwarm tools: the planner decomposes the goal
into a task graph, the implementer claims tasks / reads briefings / submits
work, the reviewer approves or bounces it, and the loop runs until the board
is green.

Setup (MiniMax platform):
    pip install "forgeswarm[demo]"
    export MINIMAX_API_KEY=sk-...           (set on Windows)
    # optional: MINIMAX_BASE_URL (default https://api.minimax.io/v1)
    #           MINIMAX_MODEL    (default MiniMax-M3)

Setup (via OpenRouter — same M3 model, lower minimum top-up):
    export MINIMAX_API_KEY=sk-or-...
    export MINIMAX_BASE_URL=https://openrouter.ai/api/v1
    export MINIMAX_MODEL=minimax/minimax-m3

Run:
    python examples/minimax_swarm_demo.py "Build a CLI pomodoro timer in Python"
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
MAX_STEPS_PER_TURN = 25
MAX_ROUNDS = 6

llm = OpenAI(api_key=os.environ["MINIMAX_API_KEY"], base_url=BASE_URL)


def mcp_tools_to_openai(tools) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in tools
    ]


def tool_result_text(result) -> str:
    if result.structuredContent is not None:
        return json.dumps(result.structuredContent)
    text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
    return ("ERROR: " + text) if result.isError else text


async def run_agent(session: ClientSession, openai_tools: list[dict],
                    name: str, instructions: str) -> str:
    """One agent turn: an M3 tool-calling loop bridged onto the MCP session."""
    messages = [
        {"role": "system",
         "content": f"You are '{name}', an autonomous agent in a ForgeSwarm engineering "
                    "swarm. Use the provided tools to act; keep text replies brief."},
        {"role": "user", "content": instructions},
    ]
    for _ in range(MAX_STEPS_PER_TURN):
        response = llm.chat.completions.create(
            model=MODEL, messages=messages, tools=openai_tools, temperature=0.2,
        )
        msg = response.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content or ""
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            result = await session.call_tool(tc.function.name, args)
            text = tool_result_text(result)
            status = "ERR" if result.isError else "ok"
            print(f"  [{name}] -> {tc.function.name}({json.dumps(args)[:100]}) [{status}] {text[:150]}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": text[:8000],
            })
    return "(turn ended: step limit reached)"


async def get_prompt_text(session: ClientSession, name: str, args: dict) -> str:
    prompt = await session.get_prompt(name, {k: str(v) for k, v in args.items()})
    return "\n".join(m.content.text for m in prompt.messages if hasattr(m.content, "text"))


async def fetch(session: ClientSession, tool: str, **kwargs):
    result = await session.call_tool(tool, kwargs)
    if result.isError:
        raise RuntimeError(f"{tool} failed: {tool_result_text(result)}")
    sc = result.structuredContent
    if sc is not None:
        return sc["result"] if set(sc.keys()) == {"result"} else sc
    # Plain `dict` return types aren't put in structuredContent by FastMCP.
    return json.loads(result.content[0].text)


async def main() -> None:
    goal = sys.argv[1] if len(sys.argv) > 1 else "Build a CLI pomodoro timer in Python"
    db = Path(tempfile.mkdtemp()) / "swarm.db"
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "forgeswarm"],
        env={**os.environ, "FORGESWARM_DB": str(db)},
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            openai_tools = mcp_tools_to_openai((await session.list_tools()).tools)

            print(f"\n##### PHASE 1 — planner decomposes: {goal!r}\n")
            planner_prompt = await get_prompt_text(session, "planner",
                                                   {"goal": goal, "constraints": ""})
            print(await run_agent(session, openai_tools, "m3-planner", planner_prompt))

            projects = await fetch(session, "list_projects")
            if not projects:
                sys.exit("Planner did not create a project; check the model output above.")
            project_id = projects[-1]["id"]

            print(f"\n##### PHASE 2 — implement/review loop on project {project_id}\n")
            for round_no in range(1, MAX_ROUNDS + 1):
                graph = await fetch(session, "get_task_graph", project_id=project_id)
                if all(t["status"] == "done" for t in graph["tasks"]):
                    break
                print(f"--- round {round_no}: implementer ---")
                impl_prompt = await get_prompt_text(
                    session, "implementer",
                    {"project_id": project_id, "agent_id": "m3-impl-1"})
                print(await run_agent(session, openai_tools, "m3-impl-1", impl_prompt))

                print(f"--- round {round_no}: reviewer ---")
                rev_prompt = await get_prompt_text(
                    session, "reviewer",
                    {"project_id": project_id, "agent_id": "m3-reviewer-1"})
                print(await run_agent(session, openai_tools, "m3-reviewer-1", rev_prompt))

            print("\n##### FINAL BOARD\n")
            graph = await fetch(session, "get_task_graph", project_id=project_id)
            print(json.dumps(graph, indent=2))

            print("\n##### STANDUP (M3 writes it from live board state)\n")
            standup_prompt = await get_prompt_text(session, "standup_summary",
                                                   {"project_id": project_id})
            standup = llm.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": standup_prompt}],
            )
            print(standup.choices[0].message.content)


if __name__ == "__main__":
    asyncio.run(main())
