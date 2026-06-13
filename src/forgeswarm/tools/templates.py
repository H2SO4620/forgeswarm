"""Workflow templates: battle-tested task graphs ready for submit_plan.

Each template carries a recommended swarm composition and a dependency-ordered
task list shaped exactly like submit_plan's input — a planner adapts the
titles/descriptions to the concrete goal and submits.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..store import Store, StoreError

TEMPLATES: dict[str, dict] = {
    "ship-feature": {
        "name": "ship-feature",
        "description": "Take a feature from design to reviewed, tested, documented code.",
        "recommended_swarm": [
            {"agent_id": "planner-1", "role": "planner"},
            {"agent_id": "impl-1", "role": "implementer"},
            {"agent_id": "reviewer-1", "role": "reviewer"},
        ],
        "tasks": [
            {"title": "Design: interface and data model", "priority": 1, "role": "implementer",
             "description": "Specify the public interface, data shapes, and edge cases."
                            " Save the design to shared context and record_decision for any"
                            " architectural choice.", "depends_on": []},
            {"title": "Implement the feature", "priority": 1, "role": "implementer",
             "description": "Build to the design from the dependency's summary and shared"
                            " context. Keep changes scoped; note deviations from the design"
                            " via save_context.", "depends_on": [0]},
            {"title": "Write tests and run checks", "priority": 1, "role": "implementer",
             "description": "Cover the happy path and the edge cases named in the design."
                            " Run them via run_checks so the evidence lands on the task.",
             "depends_on": [1]},
            {"title": "Update docs and usage examples", "priority": 2, "role": "implementer",
             "description": "Document the new behavior where users will look for it.",
             "depends_on": [1]},
        ],
    },
    "refactor-module": {
        "name": "refactor-module",
        "description": "Restructure code safely: pin behavior first, then change shape.",
        "recommended_swarm": [
            {"agent_id": "impl-1", "role": "implementer"},
            {"agent_id": "reviewer-1", "role": "reviewer"},
        ],
        "tasks": [
            {"title": "Characterize current behavior with tests", "priority": 1,
             "role": "implementer",
             "description": "Add tests that pin the module's observable behavior BEFORE any"
                            " change. Run them via run_checks; they are the safety net.",
             "depends_on": []},
            {"title": "Agree the target structure", "priority": 1, "role": "implementer",
             "description": "Open a discussion on the target design, gather positions, and"
                            " resolve_discussion so the choice is binding.", "depends_on": [0]},
            {"title": "Refactor incrementally", "priority": 1, "role": "implementer",
             "description": "Apply the agreed structure in small steps, running the"
                            " characterization tests via run_checks after each.",
             "depends_on": [1]},
            {"title": "Verify no behavior change and clean up", "priority": 2,
             "role": "implementer",
             "description": "Full check run, remove dead code, update any affected docs.",
             "depends_on": [2]},
        ],
    },
    "debug-issue": {
        "name": "debug-issue",
        "description": "Drive a bug from report to fixed, regression-proofed, and explained.",
        "recommended_swarm": [
            {"agent_id": "impl-1", "role": "implementer"},
            {"agent_id": "reviewer-1", "role": "reviewer"},
        ],
        "tasks": [
            {"title": "Reproduce the issue", "priority": 1, "role": "implementer",
             "description": "Build a minimal, reliable reproduction (ideally a failing test"
                            " run via run_checks). Save the repro steps to shared context.",
             "depends_on": []},
            {"title": "Isolate the root cause", "priority": 1, "role": "implementer",
             "description": "Narrow to the faulty component/line. record_decision on the"
                            " diagnosed cause so the fix targets it, not a symptom.",
             "depends_on": [0]},
            {"title": "Fix and add a regression test", "priority": 1, "role": "implementer",
             "description": "Apply the smallest fix for the root cause; the repro from task 1"
                            " must now pass via run_checks.", "depends_on": [1]},
            {"title": "Write the postmortem note", "priority": 3, "role": "implementer",
             "description": "Save a short cause/fix/prevention note to shared context.",
             "depends_on": [2]},
        ],
    },
}


def register(mcp: FastMCP, store: Store) -> None:
    @mcp.tool()
    def list_workflow_templates() -> list[dict]:
        """List battle-tested engineering workflow templates with what each is for."""
        return [
            {"name": t["name"], "description": t["description"],
             "tasks": len(t["tasks"])}
            for t in TEMPLATES.values()
        ]

    @mcp.tool()
    def get_workflow_template(name: str) -> dict:
        """Get a workflow template: recommended swarm + a task list shaped for submit_plan.

        Available: ship-feature, refactor-module, debug-issue. Adapt the task
        titles/descriptions to your concrete goal, then pass the tasks straight
        to submit_plan — depends_on indexes are already wired.
        """
        if name not in TEMPLATES:
            raise StoreError(f"Unknown template {name!r}. Available: {sorted(TEMPLATES)}.")
        return TEMPLATES[name]
