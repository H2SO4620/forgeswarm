"""End-to-end tests: a real MCP client session against the ForgeSwarm server."""

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from forgeswarm.server import create_server

pytestmark = pytest.mark.anyio


async def call(session, tool, **kwargs):
    result = await session.call_tool(tool, kwargs)
    assert not result.isError, f"{tool} failed: {result.content}"
    sc = result.structuredContent
    if sc is not None:
        # FastMCP wraps non-object returns (lists, scalars) as {"result": ...}
        return sc["result"] if set(sc.keys()) == {"result"} else sc
    return json.loads(result.content[0].text)


@pytest.fixture()
def server(tmp_path):
    return create_server(db_path=tmp_path / "e2e.db")


async def test_tools_resources_prompts_are_exposed(server):
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        tools = {t.name for t in (await session.list_tools()).tools}
        expected = {
            "create_project", "submit_plan", "list_projects", "register_agent",
            "list_tasks", "claim_task", "update_task", "complete_task", "get_task_graph",
            "save_context", "search_context", "record_decision", "get_briefing",
            "submit_for_review", "get_review_queue", "post_review", "run_checks",
        }
        assert expected <= tools
        prompts = {p.name for p in (await session.list_prompts()).prompts}
        assert {"planner", "implementer", "reviewer", "standup_summary"} <= prompts


async def test_full_swarm_workflow_over_mcp(server, tmp_path):
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        # plan
        project = await call(session, "create_project",
                             goal="Demo goal", constraints="keep it small")
        plan = await call(session, "submit_plan", project_id=project["id"], tasks=[
            {"title": "design", "description": "write the design note"},
            {"title": "implement", "description": "build it", "depends_on": [0]},
        ])
        design_id = plan["created_tasks"][0]["id"]
        impl_id = plan["created_tasks"][1]["id"]

        # only the dependency-free task is ready
        ready = await call(session, "list_tasks", project_id=project["id"], ready_only=True)
        assert [t["id"] for t in ready] == [design_id]

        # implementer does the first task and completes it directly
        await call(session, "claim_task", task_id=design_id, agent_id="impl-1")
        await call(session, "save_context", project_id=project["id"], key="design-note",
                   content="single module", agent_id="impl-1")
        await call(session, "complete_task", task_id=design_id, agent_id="impl-1",
                   summary="design done: single module")

        # second task goes through the review loop
        await call(session, "claim_task", task_id=impl_id, agent_id="impl-1")
        briefing = await call(session, "get_briefing", task_id=impl_id)
        assert briefing["dependency_summaries"][0]["summary"].startswith("design done")

        await call(session, "submit_for_review", task_id=impl_id, agent_id="impl-1",
                   content="the diff", self_assessment="looks fine")
        queue = await call(session, "get_review_queue", project_id=project["id"])
        await call(session, "post_review", submission_id=queue[0]["id"],
                   agent_id="reviewer-1", verdict="approve")

        graph = await call(session, "get_task_graph", project_id=project["id"])
        assert all(t["status"] == "done" for t in graph["tasks"])

        # live resource reflects completion
        res = await session.read_resource(f"swarm://project/{project['id']}/status")
        status = json.loads(res.contents[0].text)
        assert status["done"] is True


async def test_run_checks_allowlist_and_execution(server, tmp_path):
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        project = await call(session, "create_project", goal="g")
        plan = await call(session, "submit_plan", project_id=project["id"],
                          tasks=[{"title": "verify"}])
        task_id = plan["created_tasks"][0]["id"]
        await call(session, "claim_task", task_id=task_id, agent_id="impl-1")

        # disallowed executable is rejected
        bad = await session.call_tool("run_checks", {
            "task_id": task_id, "command": "curl http://evil.example", "cwd": str(tmp_path),
        })
        assert bad.isError

        # allowlisted command runs and reports evidence
        result = await call(session, "run_checks", task_id=task_id,
                            command="python -c \"print('checks-pass')\"", cwd=str(tmp_path))
        assert result["passed"] is True and "checks-pass" in result["stdout"]
