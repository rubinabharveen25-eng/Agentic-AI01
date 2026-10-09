"""
agents/planner.py - Planner Agent for Multi-Agent AI System.
Role: Analyze student assignments and create a prioritized plan.
Considers deadline, importance, and estimated effort.
Recommends which assignment should be completed first.
Produces a structured daily task plan in validated JSON format.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

import config
import database
import priority

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """You are the Planner Agent in the ASSIGNMENT DEADLINE AGENT multi-agent system.
Your role: Analyze student assignments and create an optimal, prioritized study and work plan.

Core Responsibilities:
1. Base your plan ONLY on the actual assignment records provided to you. Never invent or hallucinate assignments, IDs, or deadlines.
2. Identify overdue and upcoming assignments accurately.
3. Consider deadline urgency, importance rating (Urgent, High, Medium, Low), and estimated effort.
4. Recommend which assignment the student should complete first, with a clear pedagogical and practical justification.
5. Produce a structured daily task plan.
6. Clearly explain why each task has its assigned priority.
7. Return strictly valid JSON conforming to the requested schema.

Priority Rules to follow:
- Overdue tasks must be marked CRITICAL and placed first.
- Tasks due within 24 hours must be marked HIGH.
- Tasks due within 72 hours must be marked MEDIUM.
- Tasks due after 72 hours must be marked NORMAL.
- Completed assignments must NEVER be included in the pending task plan.

Your response MUST be a single valid JSON object with this exact structure:
{
  "recommended_first_assignment_id": <int or null>,
  "recommended_first_task_title": "<string>",
  "recommended_first_reason": "<detailed reason why this should be tackled first>",
  "summary": "<overview of the day's workload and recommendations>",
  "tasks": [
    {
      "assignment_id": <int>,
      "task_title": "<string>",
      "subject": "<string>",
      "deadline": "<ISO string or date time>",
      "priority": "CRITICAL" | "HIGH" | "MEDIUM" | "NORMAL",
      "recommended_action": "<concrete next step, e.g., 'Draft Section 2 outline'>",
      "estimated_effort": <float hours>,
      "reason_for_priority": "<explanation of why this task received this priority>"
    }
  ]
}
"""


class PlannerAgent:
    """Planner Agent generates prioritized daily study plans from database records."""

    def __init__(self, model_id: Optional[str] = None, api_key: Optional[str] = None):
        self.model_id = (model_id or config.get_planner_model()).strip()
        self.api_key = api_key or config.get_gemini_api_key()

    def generate_plan(
        self,
        assignments: List[Dict[str, Any]],
        available_hours: float = 8.0,
        feedback_from_reviewer: Optional[str] = None,
        db_path: str = database.DEFAULT_DB_PATH,
    ) -> Dict[str, Any]:
        """
        Generate a prioritized daily task plan from actual assignment records.
        If feedback_from_reviewer is provided, incorporate the feedback to revise the plan.
        """
        # Strictly filter out completed assignments
        pending_assignments = [
            a for a in assignments
            if str(a.get("status", "")).strip().lower() != "completed"
        ]

        if not pending_assignments:
            return {
                "success": True,
                "plan": {
                    "recommended_first_assignment_id": None,
                    "recommended_first_task_title": "None",
                    "recommended_first_reason": "All assignments are completed. Great job!",
                    "summary": "You have zero pending assignments. All caught up!",
                    "tasks": [],
                },
                "raw_response": "No pending assignments.",
            }

        # Deterministically enrich assignments
        enriched_assignments = [priority.enrich_assignment(a) for a in pending_assignments]
        sorted_assignments = priority.sort_assignments_by_priority(pending_assignments)
        deterministic_rec = priority.get_recommended_next_task(sorted_assignments)

        user_prompt_data = {
            "available_hours_today": available_hours,
            "pending_assignments_count": len(sorted_assignments),
            "pending_assignments": [
                {
                    "id": a["id"],
                    "title": a["title"],
                    "subject": a["subject"],
                    "deadline": a.get("deadline") or f"{a.get('deadline_date')}T{a.get('deadline_time')}:00",
                    "importance": a.get("importance", "Medium"),
                    "estimated_effort": a.get("estimated_effort", 1.0),
                    "deterministic_urgency": a.get("urgency", "NORMAL"),
                    "remaining_time": a.get("remaining_time", ""),
                    "is_overdue": a.get("is_overdue", False),
                }
                for a in sorted_assignments
            ],
            "deterministic_recommendation": {
                "assignment_id": deterministic_rec["id"] if deterministic_rec else None,
                "title": deterministic_rec["title"] if deterministic_rec else None,
                "reason": deterministic_rec.get("recommendation_reason", "") if deterministic_rec else None,
            },
        }

        prompt_text = (
            f"Here are the current pending student assignments from SQLite:\n"
            f"{json.dumps(user_prompt_data, indent=2)}\n\n"
            f"Generate the daily plan fitting approximately {available_hours} hours of effort. "
            f"Prioritize overdue and critical tasks first."
        )

        if feedback_from_reviewer:
            prompt_text += (
                f"\n\nIMPORTANT REVISION REQUEST FROM REVIEWER:\n"
                f"The Reviewer rejected your previous plan with this feedback:\n"
                f"'''{feedback_from_reviewer}'''\n"
                f"You MUST fix all issues identified by the Reviewer. Ensure all task IDs exist, "
                f"no completed tasks are present, all overdue assignments are CRITICAL, and all deadlines match exactly."
            )

        client = config.get_gemini_client(self.api_key)

        from google.genai import types

        gen_config = types.GenerateContentConfig(
            system_instruction=PLANNER_SYSTEM_PROMPT,
            temperature=0.2,
            response_mime_type="application/json",
        )

        response = client.models.generate_content(
            model=self.model_id,
            contents=prompt_text,
            config=gen_config,
        )

        resp_text = getattr(response, "text", "") or ""
        parsed_plan = self._parse_and_validate_json(resp_text)

        return {
            "success": True,
            "plan": parsed_plan,
            "raw_response": resp_text,
            "model_used": self.model_id,
        }

    def _parse_and_validate_json(self, raw_text: str) -> Dict[str, Any]:
        """Safely extract and parse JSON object from model response."""
        cleaned = raw_text.strip()
        # Remove markdown code fences if present
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
            if not isinstance(data, dict):
                raise ValueError("Parsed JSON is not an object.")
            if "tasks" not in data or not isinstance(data["tasks"], list):
                data["tasks"] = []
            return data
        except Exception as e:
            logger.error(f"Failed to parse JSON from Planner: {e}. Raw: {raw_text[:200]}")
            # Try regex to locate outermost json object
            match = re.search(r"\{[\s\S]*\}", raw_text)
            if match:
                try:
                    data = json.loads(match.group(0))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
            raise ValueError(f"Planner did not return valid JSON: {str(e)}")
