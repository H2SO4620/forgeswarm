"""Review-loop tools.

The iterate loop is enforced server-side: a 'request_changes' verdict
automatically reassigns the task to its author with the feedback attached
and bumps the iteration counter. Self-review is rejected.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..models import Review, Submission
from ..store import Store


def register(mcp: FastMCP, store: Store) -> None:
    @mcp.tool()
    def submit_for_review(
        task_id: int, agent_id: str, content: str, self_assessment: str = ""
    ) -> Submission:
        """Submit your claimed task's work product for peer review.

        `content` is what the reviewer will judge: a diff, a list of changed
        files with explanations, or a results summary. `self_assessment` is
        your honest note on risks and what you did not verify. The task moves
        to in_review until a different agent posts a verdict.
        """
        return store.submit_for_review(
            task_id, agent_id, content=content, self_assessment=self_assessment
        )

    @mcp.tool()
    def get_review_queue(project_id: int) -> list[dict]:
        """List submissions waiting for review on this project.

        Includes each submission's task title and iteration count, so reviewers
        can prioritise work that has already bounced.
        """
        return store.review_queue(project_id)

    @mcp.tool()
    def post_review(
        submission_id: int, agent_id: str, verdict: str, comments: str = ""
    ) -> Review:
        """Post a verdict on a submission: 'approve' or 'request_changes'.

        approve completes the task. request_changes sends it straight back to
        the author as in_progress with your comments attached to their next
        briefing — be specific and actionable. You cannot review your own
        submission.
        """
        return store.post_review(submission_id, reviewer=agent_id, verdict=verdict, comments=comments)
