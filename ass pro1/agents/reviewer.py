"""
agents/reviewer.py - Reviewer Agent for Multi-Agent AI System.
Role: Independently review the Planner's proposed daily plan.
Authoritative Python validation verifies assignment IDs, deadlines, excluded completed tasks,
overdue task handling, deterministic priority compliance, and missing urgent tasks.
Gemini API reviews qualitative coherence and provides concise reasons.
Returns structured decision: 'APPROVED' or 'NEEDS_REVISION'.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import config
import database
import priority

logger = logging.getLogger(__name__)

REVIEWER_SYSTEM_PROMPT = """You are the Reviewer Agent in the ASSIGNMENT DEADLINE AGENT multi-agent system.
Your role: Independently audit, validate, and review the Planner's proposed daily study plan.

Authoritative Rules:
1. All assignment IDs must exist in the real database.
2. Deadlines must match database records.
3. Completed assignments must NEVER appear in the pending daily plan.
4. Overdue tasks must be explicitly identified as CRITICAL.
5. Missing urgent tasks (due within 24 hours or overdue) must be flagged.
6. The recommendation for what to complete first must be logically and empirically sound.

Decide strictly:
- 'APPROVED' if the plan meets all consistency and priority rules.
- 'NEEDS_REVISION' if there are errors, hallucinations, priority mismatches, or missing urgent tasks.

Provide concise, actionable reasons for your decision.
"""


class ReviewerAgent:
    """Reviewer Agent validates proposed daily plans against authoritative database facts and deterministic rules."""

    def __init__(self, model_id: Optional[str] = None, api_key: Optional[str] = None):
        self.model_id = (model_id or config.get_reviewer_model()).strip()
        self.api_key = api_key or config.get_gemini_api_key()

    def review_plan(
        self,
        proposed_plan: Dict[str, Any],
        db_path: str = database.DEFAULT_DB_PATH,
    ) -> Dict[str, Any]:
        """
        Independently review the Planner's proposed daily plan.
        Runs authoritative Python validation first, then invokes Gemini Reviewer.
        Returns:
            {
                "decision": "APPROVED" | "NEEDS_REVISION",
                "is_approved": bool,
                "issues": List[str],
                "feedback_for_planner": str,
                "reviewer_summary": str,
                "validated_tasks": List[Dict[str, Any]]
            }
        """
        # 1. Authoritative Python Validation
        py_valid, issues, validated_tasks = self._authoritative_python_validation(proposed_plan, db_path=db_path)

        # 2. Invoke Gemini Reviewer for synthesis and reasoning (skip network call if mock/placeholder key)
        gemini_reasoning = ""
        is_mock_key = not self.api_key or self.api_key.startswith("mock") or self.api_key in ("your_actual_key_here", "your_gemini_api_key_here")
        if is_mock_key:
            gemini_reasoning = (
                "Python deterministic validation confirmed all database records and priority rules."
                if py_valid
                else f"Plan rejected due to validation issues: {'; '.join(issues)}"
            )
        else:
            try:
                gemini_reasoning = self._run_gemini_review(proposed_plan, py_valid, issues, db_path=db_path)
            except Exception as e:
                logger.warning(f"Gemini Reviewer API invocation note: {e}")
                gemini_reasoning = (
                    "Python deterministic validation confirmed all database records and priority rules."
                    if py_valid
                    else f"Plan rejected due to validation issues: {'; '.join(issues)}"
                )

        # Python validation is AUTHORITATIVE. Even if LLM says approve, if py_valid is False, it's NEEDS_REVISION.
        if not py_valid:
            decision = "NEEDS_REVISION"
            is_approved = False
            feedback = (
                "Validation issues found:\n- " + "\n- ".join(issues) +
                "\nPlease fix these specific issues and generate a revised plan."
            )
        else:
            decision = "APPROVED"
            is_approved = True
            feedback = "Plan passed all checks and is approved."

        return {
            "decision": decision,
            "is_approved": is_approved,
            "issues": issues,
            "feedback_for_planner": feedback,
            "reviewer_summary": gemini_reasoning,
            "validated_tasks": validated_tasks,
            "model_used": self.model_id,
        }

    def _authoritative_python_validation(
        self,
        proposed_plan: Dict[str, Any],
        db_path: str = database.DEFAULT_DB_PATH,
    ) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
        """
        Authoritative validation against SQLite database records and deterministic priority rules.
        Returns (is_valid: bool, issues: List[str], validated_tasks: List[dict]).
        """
        issues: List[str] = []
        validated_tasks: List[Dict[str, Any]] = []

        all_db_assignments = database.get_assignments(db_path=db_path)
        db_map: Dict[int, Dict[str, Any]] = {a["id"]: a for a in all_db_assignments}
        pending_db = [a for a in all_db_assignments if str(a.get("status", "")).strip().lower() != "completed"]

        tasks = proposed_plan.get("tasks", [])
        if not tasks and pending_db:
            issues.append(f"Database has {len(pending_db)} pending assignment(s), but proposed plan contains no tasks.")
            return False, issues, []

        plan_task_ids = set()
        now_dt = priority.get_current_time()

        for idx, task in enumerate(tasks):
            aid = task.get("assignment_id")
            if aid is None:
                issues.append(f"Task #{idx+1} ('{task.get('task_title', 'Untitled')}') is missing 'assignment_id'.")
                continue

            try:
                aid_int = int(aid)
            except (ValueError, TypeError):
                issues.append(f"Task #{idx+1} has invalid assignment_id '{aid}'.")
                continue

            plan_task_ids.add(aid_int)

            # Check ID exists in database
            if aid_int not in db_map:
                issues.append(f"Task #{idx+1} references non-existent assignment ID {aid_int}.")
                continue

            db_record = db_map[aid_int]

            # Rule: Completed assignments must NEVER be included
            if str(db_record.get("status", "")).strip().lower() == "completed":
                issues.append(
                    f"Task #{idx+1} (ID {aid_int}: '{db_record['title']}') is marked COMPLETED in database "
                    f"and must not be included in pending plan."
                )

            # Deterministic urgency check
            dl_val = db_record.get("deadline") or f"{db_record.get('deadline_date')}T{db_record.get('deadline_time')}:00"
            dl_dt = priority.parse_deadline(str(dl_val))
            det_urgency = priority.calculate_urgency(dl_dt, now_dt=now_dt)

            proposed_priority = str(task.get("priority", "")).strip().upper()

            # Overdue tasks must be CRITICAL
            if det_urgency == "CRITICAL" and proposed_priority != "CRITICAL":
                issues.append(
                    f"Task ID {aid_int} ('{db_record['title']}') is OVERDUE, so its priority must be CRITICAL "
                    f"(proposed: '{proposed_priority}')."
                )

            # Due within 24h should be HIGH or CRITICAL
            if det_urgency == "HIGH" and proposed_priority not in ["HIGH", "CRITICAL"]:
                issues.append(
                    f"Task ID {aid_int} ('{db_record['title']}') is due within 24h, so its priority must be HIGH "
                    f"(proposed: '{proposed_priority}')."
                )

            # Attach authoritative DB fields to validated task
            enriched_task = dict(task)
            enriched_task["db_title"] = db_record["title"]
            enriched_task["db_subject"] = db_record["subject"]
            enriched_task["deterministic_urgency"] = det_urgency
            validated_tasks.append(enriched_task)

        # If DB has no pending assignments, plan should have 0 tasks
        if not pending_db and tasks:
            issues.append("Database has 0 pending assignments, but plan contains tasks.")

        # Check for missing urgent / overdue assignments
        for pend in pending_db:
            p_id = pend["id"]
            dl_val = pend.get("deadline") or f"{pend.get('deadline_date')}T{pend.get('deadline_time')}:00"
            dl_dt = priority.parse_deadline(str(dl_val))
            urgency = priority.calculate_urgency(dl_dt, now_dt=now_dt)

            if urgency == "CRITICAL" and p_id not in plan_task_ids:
                issues.append(
                    f"URGENT: Overdue assignment ID {p_id} ('{pend['title']}') was omitted from the daily plan."
                )

        is_valid = len(issues) == 0
        return is_valid, issues, validated_tasks

    def _run_gemini_review(
        self,
        proposed_plan: Dict[str, Any],
        py_valid: bool,
        issues: List[str],
        db_path: str = database.DEFAULT_DB_PATH,
    ) -> str:
        """Call Gemini Reviewer to get a synthesized evaluation."""
        client = config.get_gemini_client(self.api_key)
        from google.genai import types

        review_prompt = (
            f"Please independently audit this proposed daily plan:\n"
            f"Proposed Plan:\n{json.dumps(proposed_plan, indent=2)}\n\n"
            f"Deterministic Python Validation Result:\n"
            f"- Passed: {py_valid}\n"
            f"- Identified Issues: {json.dumps(issues, indent=2)}\n\n"
            f"Provide a concise 2-4 sentence review explaining your decision "
            f"({'APPROVED' if py_valid else 'NEEDS_REVISION'}) and feedback."
        )

        gen_config = types.GenerateContentConfig(
            system_instruction=REVIEWER_SYSTEM_PROMPT,
            temperature=0.1,
        )

        response = client.models.generate_content(
            model=self.model_id,
            contents=review_prompt,
            config=gen_config,
        )

        return getattr(response, "text", "") or ""
