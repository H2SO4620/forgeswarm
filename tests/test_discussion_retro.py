"""Tests for discussions/consensus, workflow templates, and the retrospective."""

import pytest

from forgeswarm.store import Store, StoreError
from forgeswarm.tools.templates import TEMPLATES


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def project(store):
    return store.create_project("Build a thing")


def test_discussion_resolution_records_binding_decision(store, project):
    disc = store.open_discussion(project.id, "SQLite or Postgres?", agent_id="impl-1")
    store.post_to_discussion(disc.id, "impl-1", "SQLite: zero ops, fits the scale")
    store.post_to_discussion(disc.id, "impl-2", "Postgres: we may need concurrency")
    result = store.resolve_discussion(disc.id, "impl-1", resolution="Use SQLite",
                                      rationale="scale is small")
    assert result["discussion"]["status"] == "resolved"
    # the consensus is now a recorded decision with the debate digest
    decisions = store.list_decisions(project.id)
    assert decisions[-1].decision == "Use SQLite"
    assert "impl-2: Postgres" in decisions[-1].rationale

    # and it lands in briefings for new tasks
    t = store.create_task(project.id, "implement storage")
    briefing = store.get_briefing(t.id)
    assert any(d.decision == "Use SQLite" for d in briefing.decisions)


def test_monologue_cannot_resolve(store, project):
    disc = store.open_discussion(project.id, "topic", agent_id="impl-1")
    store.post_to_discussion(disc.id, "impl-1", "my view")
    store.post_to_discussion(disc.id, "impl-1", "still my view")
    with pytest.raises(StoreError, match="2 distinct agents"):
        store.resolve_discussion(disc.id, "impl-1", resolution="decided alone")


def test_resolved_discussion_is_closed(store, project):
    disc = store.open_discussion(project.id, "topic")
    store.post_to_discussion(disc.id, "a1", "x")
    store.post_to_discussion(disc.id, "a2", "y")
    store.resolve_discussion(disc.id, "a1", resolution="x")
    with pytest.raises(StoreError, match="resolved"):
        store.post_to_discussion(disc.id, "a3", "late take")
    with pytest.raises(StoreError, match="already resolved"):
        store.resolve_discussion(disc.id, "a2", resolution="y")


def test_templates_are_valid_submit_plan_payloads(store, project):
    for name, template in TEMPLATES.items():
        created = []
        for i, t in enumerate(template["tasks"]):
            deps = [created[idx].id for idx in t.get("depends_on", [])]
            assert all(0 <= idx < i for idx in t.get("depends_on", [])), \
                f"{name}: forward/self dependency at task {i}"
            created.append(store.create_task(
                project.id, title=t["title"], description=t.get("description", ""),
                role=t.get("role", "any"), priority=t.get("priority", 2), depends_on=deps,
            ))
        assert template["recommended_swarm"], f"{name} has no swarm recommendation"


def test_retrospective_aggregates(store, project):
    t1 = store.create_task(project.id, "easy task")
    store.claim_task(t1.id, "impl-1")
    store.complete_task(t1.id, "impl-1", summary="done")

    t2 = store.create_task(project.id, "bouncy task")
    store.claim_task(t2.id, "impl-2")
    sub = store.submit_for_review(t2.id, "impl-2", content="v1")
    store.post_review(sub.id, reviewer="rev-1", verdict="request_changes", comments="redo")
    sub2 = store.submit_for_review(t2.id, "impl-2", content="v2")
    store.post_review(sub2.id, reviewer="rev-1", verdict="approve")
    store.record_check(t2.id, "pytest", exit_code=0, stdout="ok", stderr="",
                       duration_seconds=1.0)

    retro = store.get_retrospective(project.id)
    assert retro["totals"]["tasks"] == 2 and retro["totals"]["done"] == 2
    assert retro["totals"]["review_bounce_rate"] == 0.5
    assert retro["totals"]["check_pass_rate"] == 1.0
    assert retro["totals"]["total_iterations"] == 1

    agents = {a["agent_id"]: a for a in retro["agents"]}
    assert agents["impl-1"]["tasks_completed"] == 1
    assert agents["impl-2"]["changes_requested_received"] == 1
    assert agents["rev-1"]["reviews_given"] == 2

    assert retro["hotspots"] == [{"id": t2.id, "title": "bouncy task", "iterations": 1}]
