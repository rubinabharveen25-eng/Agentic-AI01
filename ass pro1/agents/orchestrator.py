"""
agents/orchestrator.py - Multi-Agent AI Orchestrator for Assignment Deadline Agent.
Orchestrates Planner, Worker, and Reviewer agents in a structured pipeline:
Student Request -> Worker (Data Retrieval) -> Planner (Daily Plan) ->
Reviewer (Validation) -> [Needs Revision -> Planner retries once -> Reviewer] -> Validated Result.
Limits revision loop to exactly 1 retry to prevent infinite loops or excessive API usage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config
import database
from agents.planner import PlannerAgent
from agents.reviewer import ReviewerAgent
from agents.worker import WorkerAgent

logger = logging.getLogger(__name__)


@dataclass
class WorkflowStageLog:
    """Represents a single step or stage in the multi-agent execution pipeline."""
    stage_name: str
    agent_role: str
    status: str  # "IN_PROGRESS", "SUCCESS", "WARNING", "NEEDS_REVISION", "ERROR"
    message: str
    details: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class MultiAgentWorkflowResult:
    """Structured result returned by the multi-agent orchestrator."""
    success: bool
    final_plan: Dict[str, Any]
    reviewer_decision: str  # "APPROVED" or "NEEDS_REVISION"
    revision_occurred: bool
    revision_count: int
    stages: List[WorkflowStageLog]
    error_message: Optional[str] = None


class Orchestrator:
    """Coordinates Planner, Worker, and Reviewer agents with a controlled one-retry revision loop."""

    def __init__(
        self,
        planner_model: Optional[str] = None,
        worker_model: Optional[str] = None,
        reviewer_model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or config.get_gemini_api_key()
        self.planner = PlannerAgent(model_id=planner_model, api_key=self.api_key)
        self.worker = WorkerAgent(model_id=worker_model, api_key=self.api_key)
        self.reviewer = ReviewerAgent(model_id=reviewer_model, api_key=self.api_key)

    def run_daily_planner_pipeline(
        self,
        available_hours: float = 8.0,
        db_path: str = database.DEFAULT_DB_PATH,
    ) -> MultiAgentWorkflowResult:
        """
        Executes the complete multi-agent workflow:
        1. Worker Agent queries SQLite for fresh assignments.
        2. Planner Agent analyzes assignments and produces initial daily plan.
        3. Reviewer Agent independently audits plan against DB and deterministic rules.
        4. If Reviewer rejects: Planner retries once with feedback, Reviewer re-audits.
        5. Validated result returned to dashboard.
        """
        stages: List[WorkflowStageLog] = []

        # --- Stage 1: Worker Agent Data Retrieval ---
        stages.append(
            WorkflowStageLog(
                stage_name="Data Retrieval",
                agent_role="Worker",
                status="IN_PROGRESS",
                message="Retrieving active assignments and calculating urgency from SQLite...",
            )
        )

        try:
            worker_data = self.worker.execute_direct_tool("get_assignments", {"status": "Pending"}, db_path=db_path)
            pending_assignments = worker_data.get("assignments", [])
            stages[-1].status = "SUCCESS"
            stages[-1].message = f"Retrieved {len(pending_assignments)} pending assignment(s) from SQLite."
            stages[-1].details = {"count": len(pending_assignments)}
        except Exception as exc:
            err = f"Worker failed to retrieve assignments: {str(exc)}"
            logger.error(err)
            stages[-1].status = "ERROR"
            stages[-1].message = err
            return MultiAgentWorkflowResult(
                success=False,
                final_plan={},
                reviewer_decision="NEEDS_REVISION",
                revision_occurred=False,
                revision_count=0,
                stages=stages,
                error_message=err,
            )

        # --- Stage 2: Planner Agent Plan Generation ---
        stages.append(
            WorkflowStageLog(
                stage_name="Plan Generation",
                agent_role="Planner",
                status="IN_PROGRESS",
                message="Analyzing deadlines, importance, and effort to generate structured plan...",
            )
        )

        try:
            planner_res = self.planner.generate_plan(
                assignments=pending_assignments,
                available_hours=available_hours,
                db_path=db_path,
            )
            proposed_plan = planner_res.get("plan", {})
            stages[-1].status = "SUCCESS"
            tasks_count = len(proposed_plan.get("tasks", []))
            stages[-1].message = f"Generated initial plan with {tasks_count} prioritized task(s)."
            stages[-1].details = {"tasks_count": tasks_count, "recommended_first": proposed_plan.get("recommended_first_task_title")}
        except Exception as exc:
            err = f"Planner failed to generate plan: {str(exc)}"
            logger.error(err)
            stages[-1].status = "ERROR"
            stages[-1].message = err
            return MultiAgentWorkflowResult(
                success=False,
                final_plan={},
                reviewer_decision="NEEDS_REVISION",
                revision_occurred=False,
                revision_count=0,
                stages=stages,
                error_message=err,
            )

        # --- Stage 3: Reviewer Agent Independent Validation ---
        stages.append(
            WorkflowStageLog(
                stage_name="Independent Audit",
                agent_role="Reviewer",
                status="IN_PROGRESS",
                message="Auditing proposed plan against database records and deterministic priority rules...",
            )
        )

        try:
            review_res = self.reviewer.review_plan(proposed_plan, db_path=db_path)
            is_approved = review_res.get("is_approved", False)
            decision = review_res.get("decision", "NEEDS_REVISION")
            issues = review_res.get("issues", [])

            if is_approved:
                stages[-1].status = "SUCCESS"
                stages[-1].message = "Plan APPROVED: Verified against database IDs, deadlines, and priority rules."
                stages[-1].details = {"decision": decision, "summary": review_res.get("reviewer_summary")}
                return MultiAgentWorkflowResult(
                    success=True,
                    final_plan=proposed_plan,
                    reviewer_decision="APPROVED",
                    revision_occurred=False,
                    revision_count=0,
                    stages=stages,
                )
            else:
                stages[-1].status = "WARNING"
                stages[-1].message = f"Plan rejected: {len(issues)} issue(s) identified. Requesting 1 revision from Planner."
                stages[-1].details = {"issues": issues, "feedback": review_res.get("feedback_for_planner")}
        except Exception as exc:
            err = f"Reviewer validation encountered an error: {str(exc)}"
            logger.error(err)
            stages[-1].status = "ERROR"
            stages[-1].message = err
            return MultiAgentWorkflowResult(
                success=False,
                final_plan=proposed_plan,
                reviewer_decision="NEEDS_REVISION",
                revision_occurred=False,
                revision_count=0,
                stages=stages,
                error_message=err,
            )

        # --- Stage 4: Controlled One-Retry Revision Loop ---
        stages.append(
            WorkflowStageLog(
                stage_name="Plan Revision (Attempt 1 of 1)",
                agent_role="Planner",
                status="IN_PROGRESS",
                message="Revising plan based on Reviewer's specific audit feedback...",
            )
        )

        try:
            revised_res = self.planner.generate_plan(
                assignments=pending_assignments,
                available_hours=available_hours,
                feedback_from_reviewer=review_res.get("feedback_for_planner"),
                db_path=db_path,
            )
            revised_plan = revised_res.get("plan", {})
            stages[-1].status = "SUCCESS"
            stages[-1].message = f"Produced revised plan with {len(revised_plan.get('tasks', []))} task(s)."
        except Exception as exc:
            err = f"Planner revision failed: {str(exc)}"
            logger.error(err)
            stages[-1].status = "ERROR"
            stages[-1].message = err
            return MultiAgentWorkflowResult(
                success=False,
                final_plan=proposed_plan,
                reviewer_decision="NEEDS_REVISION",
                revision_occurred=True,
                revision_count=1,
                stages=stages,
                error_message=err,
            )

        # --- Stage 5: Final Review of Revised Plan ---
        stages.append(
            WorkflowStageLog(
                stage_name="Re-Audit of Revised Plan",
                agent_role="Reviewer",
                status="IN_PROGRESS",
                message="Conducting final audit of revised plan...",
            )
        )

        try:
            re_review = self.reviewer.review_plan(revised_plan, db_path=db_path)
            final_approved = re_review.get("is_approved", False)
            final_decision = re_review.get("decision", "NEEDS_REVISION")

            if final_approved:
                stages[-1].status = "SUCCESS"
                stages[-1].message = "Revised plan APPROVED by Reviewer."
                stages[-1].details = {"decision": final_decision}
            else:
                stages[-1].status = "WARNING"
                stages[-1].message = f"Revised plan still has issues: {'; '.join(re_review.get('issues', []))}"
                stages[-1].details = {"issues": re_review.get("issues", [])}

            return MultiAgentWorkflowResult(
                success=final_approved,
                final_plan=revised_plan if final_approved else proposed_plan,
                reviewer_decision=final_decision,
                revision_occurred=True,
                revision_count=1,
                stages=stages,
                error_message=None if final_approved else f"Plan required revision: {'; '.join(re_review.get('issues', []))}",
            )
        except Exception as exc:
            err = f"Reviewer re-audit error: {str(exc)}"
            logger.error(err)
            stages[-1].status = "ERROR"
            stages[-1].message = err
            return MultiAgentWorkflowResult(
                success=False,
                final_plan=revised_plan,
                reviewer_decision="NEEDS_REVISION",
                revision_occurred=True,
                revision_count=1,
                stages=stages,
                error_message=err,
            )

    def run_chat_assistant(
        self,
        user_message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        db_path: str = database.DEFAULT_DB_PATH,
    ) -> Dict[str, Any]:
        """
        Processes conversational student questions through the Worker agent's tool-calling loop.
        Ensures factual grounding with real SQLite database tools.
        """
        return self.worker.execute_task(
            user_prompt=user_message,
            conversation_history=conversation_history,
            db_path=db_path,
        )
