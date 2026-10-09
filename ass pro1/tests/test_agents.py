"""
test_agents.py - Unit tests for Planner, Worker, Reviewer, and Orchestrator.
Tests Planner output validation, Reviewer rejection of incorrect plans,
authoritative deterministic rules, one-retry revision workflow,
and Gemini API connection testing with mock objects.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest

import config
import database
import priority
from agents.planner import PlannerAgent
from agents.worker import WorkerAgent
from agents.reviewer import ReviewerAgent
from agents.orchestrator import Orchestrator


# --- API Connection Tests ---

def test_api_connection_missing_key():
    """Verify test_api_connection cleanly diagnoses missing key."""
    success, msg, diag = config.test_api_connection(api_key="")
    assert success is False
    assert "missing" in msg.lower()


def test_api_connection_invalid_key(monkeypatch: pytest.MonkeyPatch):
    """Verify test_api_connection handles API errors cleanly."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("403 Forbidden: API_KEY_INVALID")
    monkeypatch.setattr(config, "get_gemini_client", lambda key: mock_client)

    success, msg, diag = config.test_api_connection(api_key="AIzaSyBadKey")
    assert success is False
    assert "Invalid Gemini API Key" in msg


def test_api_connection_success(monkeypatch: pytest.MonkeyPatch):
    """Verify test_api_connection reports success when live/mock API responds."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "OK"
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(config, "get_gemini_client", lambda key: mock_client)

    success, msg, diag = config.test_api_connection(api_key="AIzaSyValidKey")
    assert success is True
    assert "Successfully connected" in msg


# --- Planner Agent Tests ---

def test_planner_generates_structured_plan(monkeypatch: pytest.MonkeyPatch, temp_db: str):
    """Verify PlannerAgent generates structured plan matching schema."""
    aid1 = database.add_assignment("Math Quiz", "MATH", "2026-10-12", "14:00", importance="High", db_path=temp_db)
    aid2 = database.add_assignment("Physics HW", "PHYS", "2026-10-15", "18:00", importance="Medium", db_path=temp_db)

    assignments = database.get_assignments(db_path=temp_db)

    # Mock Gemini response with structured JSON
    expected_plan = {
        "recommended_first_assignment_id": aid1,
        "recommended_first_task_title": "Math Quiz",
        "recommended_first_reason": "High priority task due soon.",
        "summary": "Plan for 2 tasks.",
        "tasks": [
            {
                "assignment_id": aid1,
                "task_title": "Math Quiz",
                "subject": "MATH",
                "deadline": "2026-10-12T14:00:00",
                "priority": "HIGH",
                "recommended_action": "Review Chapter 3",
                "estimated_effort": 1.5,
                "reason_for_priority": "Due within 24h",
            },
            {
                "assignment_id": aid2,
                "task_title": "Physics HW",
                "subject": "PHYS",
                "deadline": "2026-10-15T18:00:00",
                "priority": "NORMAL",
                "recommended_action": "Complete problems 1-5",
                "estimated_effort": 2.0,
                "reason_for_priority": "Due after 72h",
            },
        ],
    }

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(expected_plan)
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(config, "get_gemini_client", lambda key: mock_client)

    planner = PlannerAgent(api_key="mock_key")
    res = planner.generate_plan(assignments, available_hours=8.0, db_path=temp_db)

    assert res["success"] is True
    plan = res["plan"]
    assert plan["recommended_first_assignment_id"] == aid1
    assert len(plan["tasks"]) == 2
    assert plan["tasks"][0]["priority"] == "HIGH"


# --- Reviewer Agent Tests ---

def test_reviewer_approves_valid_plan(temp_db: str, monkeypatch: pytest.MonkeyPatch):
    """Verify ReviewerAgent approves a plan that strictly matches database facts."""
    # Seed pending assignment due in 10h (urgency HIGH)
    now_dt = priority.get_current_time()
    due_dt = now_dt + timedelta(hours=10)
    aid = database.add_assignment(
        "Urgent Essay", "ENG", due_dt.strftime("%Y-%m-%d"), due_dt.strftime("%H:%M"), db_path=temp_db
    )

    valid_plan = {
        "recommended_first_assignment_id": aid,
        "recommended_first_task_title": "Urgent Essay",
        "recommended_first_reason": "High priority essay due soon.",
        "summary": "1 urgent task.",
        "tasks": [
            {
                "assignment_id": aid,
                "task_title": "Urgent Essay",
                "subject": "ENG",
                "deadline": due_dt.strftime("%Y-%m-%dT%H:%M:00"),
                "priority": "HIGH",
                "recommended_action": "Draft intro",
                "estimated_effort": 2.0,
                "reason_for_priority": "Due within 24h",
            }
        ],
    }

    reviewer = ReviewerAgent(api_key="mock_key")
    # Mock Gemini review synthesis to prevent API call
    monkeypatch.setattr(reviewer, "_run_gemini_review", lambda *args, **kwargs: "Plan matches database and is approved.")

    result = reviewer.review_plan(valid_plan, db_path=temp_db)
    assert result["is_approved"] is True
    assert result["decision"] == "APPROVED"
    assert len(result["issues"]) == 0


def test_reviewer_rejects_hallucinated_assignment_id(temp_db: str):
    """Verify ReviewerAgent rejects a plan with non-existent assignment IDs."""
    database.add_assignment("Real Task", "CS", "2026-10-20", "12:00", db_path=temp_db)
    fake_plan = {
        "tasks": [
            {
                "assignment_id": 9999,  # Non-existent ID
                "task_title": "Invented Task",
                "priority": "NORMAL",
            }
        ]
    }
    reviewer = ReviewerAgent(api_key="mock_key")
    result = reviewer.review_plan(fake_plan, db_path=temp_db)
    assert result["is_approved"] is False
    assert result["decision"] == "NEEDS_REVISION"
    assert any("non-existent" in issue for issue in result["issues"])


def test_reviewer_rejects_completed_assignment(temp_db: str):
    """Verify ReviewerAgent strictly rejects any plan containing a completed assignment."""
    database.add_assignment("Active Task", "MATH", "2026-10-20", "12:00", db_path=temp_db)
    aid = database.add_assignment("Done Quiz", "CS", "2026-10-15", "12:00", db_path=temp_db)
    database.mark_assignment_completed(aid, db_path=temp_db)

    invalid_plan = {
        "tasks": [
            {
                "assignment_id": aid,
                "task_title": "Done Quiz",
                "priority": "NORMAL",
            }
        ]
    }
    reviewer = ReviewerAgent(api_key="mock_key")
    result = reviewer.review_plan(invalid_plan, db_path=temp_db)
    assert result["is_approved"] is False
    assert result["decision"] == "NEEDS_REVISION"
    assert any("COMPLETED" in issue for issue in result["issues"])


def test_reviewer_rejects_non_critical_overdue_assignment(temp_db: str):
    """Verify ReviewerAgent rejects an overdue task if not marked CRITICAL."""
    now_dt = priority.get_current_time()
    past_dt = now_dt - timedelta(hours=5)
    aid = database.add_assignment("Overdue Lab", "PHYS", past_dt.strftime("%Y-%m-%d"), past_dt.strftime("%H:%M"), db_path=temp_db)

    invalid_plan = {
        "tasks": [
            {
                "assignment_id": aid,
                "task_title": "Overdue Lab",
                "priority": "NORMAL",  # INCORRECT! Must be CRITICAL
            }
        ]
    }
    reviewer = ReviewerAgent(api_key="mock_key")
    result = reviewer.review_plan(invalid_plan, db_path=temp_db)
    assert result["is_approved"] is False
    assert any("OVERDUE" in issue and "CRITICAL" in issue for issue in result["issues"])


# --- Orchestrator & Revision Workflow Tests ---

def test_orchestrator_one_retry_revision_workflow(temp_db: str, monkeypatch: pytest.MonkeyPatch):
    """Verify Orchestrator executes exactly 1 revision attempt when Reviewer requests revision."""
    now_dt = priority.get_current_time()
    due_dt = now_dt + timedelta(hours=10)
    aid = database.add_assignment("History Essay", "HIST", due_dt.strftime("%Y-%m-%d"), due_dt.strftime("%H:%M"), db_path=temp_db)

    orchestrator = Orchestrator(api_key="mock_key")

    # Turn 1: Planner returns a bad plan (marked NORMAL instead of HIGH)
    bad_plan = {
        "recommended_first_assignment_id": aid,
        "recommended_first_task_title": "History Essay",
        "summary": "Bad plan",
        "tasks": [
            {
                "assignment_id": aid,
                "task_title": "History Essay",
                "subject": "HIST",
                "deadline": due_dt.strftime("%Y-%m-%dT%H:%M:00"),
                "priority": "NORMAL",  # Will fail review
                "recommended_action": "Write",
                "estimated_effort": 1.0,
                "reason_for_priority": "Normal",
            }
        ],
    }

    # Turn 2: Revised plan (corrected to HIGH)
    good_plan = {
        "recommended_first_assignment_id": aid,
        "recommended_first_task_title": "History Essay",
        "summary": "Good revised plan",
        "tasks": [
            {
                "assignment_id": aid,
                "task_title": "History Essay",
                "subject": "HIST",
                "deadline": due_dt.strftime("%Y-%m-%dT%H:%M:00"),
                "priority": "HIGH",  # Fixed!
                "recommended_action": "Write",
                "estimated_effort": 1.0,
                "reason_for_priority": "Due in 10h",
            }
        ],
    }

    planner_calls = 0

    def fake_planner_generate(assignments, available_hours=8.0, feedback_from_reviewer=None, db_path=temp_db):
        nonlocal planner_calls
        planner_calls += 1
        if feedback_from_reviewer:
            return {"success": True, "plan": good_plan}
        return {"success": True, "plan": bad_plan}

    monkeypatch.setattr(orchestrator.planner, "generate_plan", fake_planner_generate)
    monkeypatch.setattr(orchestrator.reviewer, "_run_gemini_review", lambda *a, **k: "Review complete.")

    result = orchestrator.run_daily_planner_pipeline(available_hours=8.0, db_path=temp_db)

    assert result.success is True
    assert result.revision_occurred is True
    assert result.revision_count == 1
    assert planner_calls == 2  # 1 initial + 1 revision
    assert result.reviewer_decision == "APPROVED"
    assert result.final_plan["tasks"][0]["priority"] == "HIGH"
