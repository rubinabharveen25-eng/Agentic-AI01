"""
test_priority.py - Unit tests for deterministic priority engine.
Tests urgency calculation, remaining time formatting, and recommendation sorting.
"""

from datetime import datetime, timedelta, timezone

import priority


def test_calculate_urgency_levels():
    """Verify Critical, High, Medium, Normal, and Completed thresholds."""
    now_dt = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    # 1. Overdue and pending: Critical
    overdue_dt = now_dt - timedelta(hours=2)
    assert priority.calculate_urgency(overdue_dt, now_dt=now_dt, is_completed=False) == "Critical"

    # 2. Due within 24 hours: High
    due_in_10h = now_dt + timedelta(hours=10)
    assert priority.calculate_urgency(due_in_10h, now_dt=now_dt, is_completed=False) == "High"

    # 3. Due within 72 hours: Medium
    due_in_48h = now_dt + timedelta(hours=48)
    assert priority.calculate_urgency(due_in_48h, now_dt=now_dt, is_completed=False) == "Medium"

    # 4. More than 72 hours away: Normal
    due_in_5d = now_dt + timedelta(days=5)
    assert priority.calculate_urgency(due_in_5d, now_dt=now_dt, is_completed=False) == "Normal"

    # 5. Completed: Completed regardless of date
    assert priority.calculate_urgency(overdue_dt, now_dt=now_dt, is_completed=True) == "Completed"


def test_format_remaining_time():
    """Verify human-readable remaining time and overdue detection."""
    now_dt = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    # Future deadline
    fut_dt = now_dt + timedelta(days=1, hours=3, minutes=15)
    text, is_overdue = priority.format_remaining_time(fut_dt, now_dt=now_dt)
    assert is_overdue is False
    assert "Due in" in text
    assert "1d 3h" in text

    # Overdue deadline
    past_dt = now_dt - timedelta(hours=4, minutes=30)
    text_past, is_overdue_past = priority.format_remaining_time(past_dt, now_dt=now_dt)
    assert is_overdue_past is True
    assert "Overdue by" in text_past
    assert "4h 30m" in text_past


def test_calculate_priority_score():
    """Verify score calculation gives highest weight to critical/urgent tasks."""
    now_dt = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    task_critical = {
        "deadline_date": "2026-10-10",
        "deadline_time": "10:00",
        "importance": "Urgent",
        "estimated_effort": 3.0,
        "status": "Pending",
    }
    score_critical = priority.calculate_priority_score(task_critical, now_dt=now_dt)

    task_normal = {
        "deadline_date": "2026-10-25",
        "deadline_time": "12:00",
        "importance": "Low",
        "estimated_effort": 1.0,
        "status": "Pending",
    }
    score_normal = priority.calculate_priority_score(task_normal, now_dt=now_dt)

    assert score_critical > score_normal
    assert score_critical >= 140.0  # 100 urgency + 40 importance + effort factor


def test_sort_assignments_and_recommendation():
    """Verify sorting and recommendation logic."""
    now_dt = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    tasks = [
        {
            "id": 1,
            "title": "Future Project",
            "subject": "CS300",
            "deadline_date": "2026-10-20",
            "deadline_time": "12:00",
            "importance": "Medium",
            "estimated_effort": 2.0,
            "status": "Pending",
        },
        {
            "id": 2,
            "title": "Overdue Lab Report",
            "subject": "CHEM101",
            "deadline_date": "2026-10-09",
            "deadline_time": "23:59",
            "importance": "High",
            "estimated_effort": 2.0,
            "status": "Pending",
        },
        {
            "id": 3,
            "title": "Completed Task",
            "subject": "ENG101",
            "deadline_date": "2026-10-08",
            "deadline_time": "12:00",
            "importance": "Urgent",
            "estimated_effort": 1.0,
            "status": "Completed",
        },
    ]

    sorted_tasks = priority.sort_assignments_by_priority(tasks, now_dt=now_dt)
    assert sorted_tasks[0]["id"] == 2  # Overdue is first
    assert sorted_tasks[0]["urgency"] == "Critical"
    assert sorted_tasks[0]["is_overdue"] is True

    recommended = priority.get_recommended_next_task(tasks, now_dt=now_dt)
    assert recommended is not None
    assert recommended["id"] == 2
    assert "OVERDUE" in recommended["recommendation_reason"]
