"""Unit tests for the ForgeSwarm store: lifecycle, atomicity, and the review loop."""

import pytest

from forgeswarm.store import Store, StoreError


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture()
def project(store):
    return store.create_project("Build a URL shortener", constraints="Python only")


def test_project_lifecycle(store):
    p = store.create_project("Goal", constraints="c1")
    assert p.id == 1 and p.status == "active"
    assert [x.id for x in store.list_projects()] == [1]
    assert store.set_project_status(p.id, "done").status == "done"
    with pytest.raises(StoreError):
        store.get_project(999)


def test_task_graph_and_ready_only(store, project):
    t1 = store.create_task(project.id, "design schema")
    t2 = store.create_task(project.id, "implement api", depends_on=[t1.id])
    ready = store.list_tasks(project.id, ready_only=True)
    assert [t.id for t in ready] == [t1.id]
    graph = store.task_graph(project.id)
    by_id = {t["id"]: t for t in graph["tasks"]}
    assert by_id[t2.id]["depends_on"] == [t1.id]
    assert by_id[t2.id]["ready"] is False


def test_claim_is_exclusive(store, project):
    t = store.create_task(project.id, "task")
    claimed = store.claim_task(t.id, "agent-a")
    assert claimed.status == "claimed" and claimed.claimed_by == "agent-a"
    with pytest.raises(StoreError, match="claimed"):
        store.claim_task(t.id, "agent-b")


def test_claim_blocked_by_unfinished_deps(store, project):
    t1 = store.create_task(project.id, "first")
    t2 = store.create_task(project.id, "second", depends_on=[t1.id])
    with pytest.raises(StoreError, match="not ready"):
        store.claim_task(t2.id, "agent-a")
    store.claim_task(t1.id, "agent-a")
    store.complete_task(t1.id, "agent-a", summary="done")
    assert store.claim_task(t2.id, "agent-b").claimed_by == "agent-b"


def test_expired_lease_returns_task_to_board(store, project):
    t = store.create_task(project.id, "task")
    store.claim_task(t.id, "agent-a", lease_seconds=-5)  # already expired
    ready = store.list_tasks(project.id, ready_only=True)
    assert [x.id for x in ready] == [t.id]
    assert store.get_task(t.id).claimed_by is None


def test_only_claimant_can_update_or_complete(store, project):
    t = store.create_task(project.id, "task")
    store.claim_task(t.id, "agent-a")
    with pytest.raises(StoreError, match="agent-a"):
        store.complete_task(t.id, "agent-b", summary="hijack")
    with pytest.raises(StoreError, match="agent-a"):
        store.update_task(t.id, "agent-b", status="in_progress")


def test_review_loop_request_changes_then_approve(store, project):
    t = store.create_task(project.id, "implement feature")
    store.claim_task(t.id, "impl-1")
    sub = store.submit_for_review(t.id, "impl-1", content="diff v1", self_assessment="untested")
    assert store.get_task(t.id).status == "in_review"

    # request_changes sends the task straight back to its author
    review = store.post_review(sub.id, reviewer="rev-1", verdict="request_changes",
                               comments="add tests")
    assert review.verdict == "request_changes"
    task = store.get_task(t.id)
    assert task.status == "in_progress" and task.claimed_by == "impl-1" and task.iteration == 1

    # feedback shows up in the next briefing
    briefing = store.get_briefing(t.id)
    assert any("add tests" in r.comments for r in briefing.prior_review_feedback)

    sub2 = store.submit_for_review(t.id, "impl-1", content="diff v2 with tests")
    store.post_review(sub2.id, reviewer="rev-1", verdict="approve")
    assert store.get_task(t.id).status == "done"


def test_self_review_rejected(store, project):
    t = store.create_task(project.id, "task")
    store.claim_task(t.id, "impl-1")
    sub = store.submit_for_review(t.id, "impl-1", content="work")
    with pytest.raises(StoreError, match="Self-review"):
        store.post_review(sub.id, reviewer="impl-1", verdict="approve")


def test_double_review_rejected(store, project):
    t = store.create_task(project.id, "task")
    store.claim_task(t.id, "impl-1")
    sub = store.submit_for_review(t.id, "impl-1", content="work")
    store.post_review(sub.id, reviewer="rev-1", verdict="approve")
    with pytest.raises(StoreError, match="already reviewed"):
        store.post_review(sub.id, reviewer="rev-2", verdict="approve")


def test_context_upsert_and_search(store, project):
    store.save_context(project.id, "api-schema", "v1: GET /shorten", tags=["api"], author="a1")
    store.save_context(project.id, "api-schema", "v2: POST /shorten", tags=["api"], author="a1")
    hits = store.search_context(project.id, "shorten")
    assert len(hits) == 1 and "v2" in hits[0].content
    assert store.search_context(project.id, "nonexistent-xyz") == []


def test_briefing_bundles_everything(store, project):
    store.record_decision(project.id, "Use SQLite", rationale="zero ops", author="planner")
    t1 = store.create_task(project.id, "schema")
    store.claim_task(t1.id, "a1")
    store.complete_task(t1.id, "a1", summary="two tables: users, links")
    t2 = store.create_task(project.id, "api", depends_on=[t1.id])
    store.save_context(project.id, "endpoint-notes", "use 301 redirects",
                       tags=[f"task-{t2.id}"], author="a1")

    b = store.get_briefing(t2.id)
    assert b.project.goal == "Build a URL shortener"
    assert b.dependency_summaries == [{"id": t1.id, "title": "schema",
                                       "summary": "two tables: users, links"}]
    assert any(d.decision == "Use SQLite" for d in b.decisions)
    assert any("301" in c.content for c in b.related_context)
