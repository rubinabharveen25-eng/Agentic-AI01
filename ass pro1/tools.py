"""
tools.py - Registered Tool Definitions and Execution Functions for Assignment Deadline Agent.
Validates all tool arguments strictly and interacts directly with SQLite and Priority engine.
Compatible with Google Gen AI SDK tool calling.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import database
import priority


# --- Validation Helpers ---

def validate_date(date_str: str) -> bool:
    """Validate that date_str is in YYYY-MM-DD format."""
    try:
        datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return True
    except (ValueError, AttributeError):
        return False


def validate_time(time_str: str) -> bool:
    """Validate that time_str is in HH:MM or HH:MM:SS format."""
    try:
        parts = time_str.strip().split(":")
        if len(parts) == 2:
            datetime.strptime(time_str.strip(), "%H:%M")
            return True
        elif len(parts) == 3:
            datetime.strptime(time_str.strip(), "%H:%M:%S")
            return True
        return False
    except (ValueError, AttributeError):
        return False


def validate_iso_deadline(deadline_str: str) -> bool:
    """Validate ISO deadline or YYYY-MM-DD date."""
    cleaned = deadline_str.strip()
    if "T" in cleaned or " " in cleaned:
        try:
            priority.parse_deadline(cleaned)
            return True
        except Exception:
            return False
    return validate_date(cleaned)


# --- Real Tool Execution Functions ---

def tool_get_assignments(
    status: Optional[str] = None,
    subject: Optional[str] = None,
    search: Optional[str] = None,
    db_path: str = database.DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Retrieve assignments from SQLite, enriched with deterministic priority and urgency."""
    raw = database.get_assignments(status=status, subject=subject, search=search, db_path=db_path)
    enriched = [priority.enrich_assignment(a) for a in raw]
    return {
        "success": True,
        "count": len(enriched),
        "assignments": enriched,
    }


def tool_add_assignment(
    title: str,
    subject: str,
    deadline_date: Optional[str] = None,
    deadline_time: Optional[str] = None,
    deadline: Optional[str] = None,
    description: str = "",
    importance: str = "Medium",
    estimated_effort: float = 1.0,
    db_path: str = database.DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Validate arguments and insert a new assignment into SQLite."""
    if not title or not str(title).strip():
        return {"success": False, "error": "Title cannot be empty."}
    if not subject or not str(subject).strip():
        return {"success": False, "error": "Subject cannot be empty."}

    # Handle deadline specification
    if deadline:
        if not validate_iso_deadline(deadline):
            return {"success": False, "error": f"Invalid deadline '{deadline}'. Expected ISO format YYYY-MM-DDTHH:MM."}
        final_deadline = deadline.strip()
        final_date = None
        final_time = None
    else:
        if not deadline_date or not validate_date(deadline_date):
            return {"success": False, "error": f"Invalid deadline_date '{deadline_date}'. Expected format YYYY-MM-DD."}
        d_time = deadline_time or "23:59"
        if not validate_time(d_time):
            return {"success": False, "error": f"Invalid deadline_time '{d_time}'. Expected format HH:MM."}
        t_parts = d_time.strip().split(":")
        final_time = f"{t_parts[0].zfill(2)}:{t_parts[1].zfill(2)}"
        final_date = deadline_date.strip()
        final_deadline = f"{final_date}T{final_time}:00"

    valid_importances = ["Low", "Medium", "High", "Urgent"]
    importance_clean = str(importance).strip().capitalize()
    if importance_clean not in valid_importances:
        return {
            "success": False,
            "error": f"Invalid importance '{importance}'. Must be one of: {', '.join(valid_importances)}.",
        }

    try:
        effort = float(estimated_effort)
        if effort <= 0:
            return {"success": False, "error": "estimated_effort must be greater than 0 hours."}
    except (ValueError, TypeError):
        return {"success": False, "error": "estimated_effort must be a valid number of hours."}

    assignment_id = database.add_assignment(
        title=str(title).strip(),
        subject=str(subject).strip(),
        deadline_date=final_date,
        deadline_time=final_time,
        deadline=final_deadline,
        description=str(description).strip() if description else "",
        importance=importance_clean,
        estimated_effort=effort,
        db_path=db_path,
    )

    created = database.get_assignment_by_id(assignment_id, db_path=db_path)
    enriched = priority.enrich_assignment(created) if created else {}

    return {
        "success": True,
        "assignment_id": assignment_id,
        "message": f"Assignment '{title}' (ID: {assignment_id}) created successfully in database.",
        "assignment": enriched,
    }


def tool_get_assignment_details(
    assignment_id: int,
    db_path: str = database.DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Retrieve full details of an assignment by ID."""
    try:
        aid = int(assignment_id)
    except (ValueError, TypeError):
        return {"success": False, "error": "assignment_id must be an integer."}

    assignment = database.get_assignment_by_id(aid, db_path=db_path)
    if not assignment:
        return {"success": False, "error": f"Assignment with ID {aid} does not exist in the database."}

    enriched = priority.enrich_assignment(assignment)
    records = database.get_reminder_records(assignment_id=aid, limit=10, db_path=db_path)
    enriched["reminder_history"] = records

    return {
        "success": True,
        "assignment": enriched,
    }


def tool_update_assignment(
    assignment_id: int,
    title: Optional[str] = None,
    subject: Optional[str] = None,
    description: Optional[str] = None,
    deadline: Optional[str] = None,
    deadline_date: Optional[str] = None,
    deadline_time: Optional[str] = None,
    importance: Optional[str] = None,
    estimated_effort: Optional[float] = None,
    status: Optional[str] = None,
    db_path: str = database.DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Validate and update existing assignment in SQLite."""
    try:
        aid = int(assignment_id)
    except (ValueError, TypeError):
        return {"success": False, "error": "assignment_id must be an integer."}

    existing = database.get_assignment_by_id(aid, db_path=db_path)
    if not existing:
        return {"success": False, "error": f"Assignment with ID {aid} not found in database."}

    if deadline is not None and not validate_iso_deadline(deadline):
        return {"success": False, "error": f"Invalid deadline '{deadline}'. Expected ISO format YYYY-MM-DDTHH:MM."}

    if deadline_date is not None and not validate_date(deadline_date):
        return {"success": False, "error": f"Invalid deadline_date '{deadline_date}'. Expected YYYY-MM-DD."}

    if deadline_time is not None:
        if not validate_time(deadline_time):
            return {"success": False, "error": f"Invalid deadline_time '{deadline_time}'. Expected HH:MM."}
        parts = deadline_time.strip().split(":")
        deadline_time = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"

    if importance is not None:
        importance_clean = str(importance).strip().capitalize()
        if importance_clean not in ["Low", "Medium", "High", "Urgent"]:
            return {"success": False, "error": f"Invalid importance '{importance}'."}
        importance = importance_clean

    if estimated_effort is not None:
        try:
            effort = float(estimated_effort)
            if effort <= 0:
                return {"success": False, "error": "estimated_effort must be greater than 0."}
            estimated_effort = effort
        except (ValueError, TypeError):
            return {"success": False, "error": "estimated_effort must be a number."}

    if status is not None:
        status_clean = str(status).strip().capitalize()
        if status_clean not in ["Pending", "Completed", "In progress"]:
            return {"success": False, "error": f"Status must be 'Pending', 'In Progress', or 'Completed'."}
        if status_clean == "In progress":
            status_clean = "In Progress"
        status = status_clean

    success = database.update_assignment(
        assignment_id=aid,
        title=title,
        subject=subject,
        description=description,
        deadline=deadline,
        deadline_date=deadline_date,
        deadline_time=deadline_time,
        importance=importance,
        estimated_effort=estimated_effort,
        status=status,
        db_path=db_path,
    )

    if not success:
        return {"success": False, "error": "Failed to update assignment."}

    updated = database.get_assignment_by_id(aid, db_path=db_path)
    return {
        "success": True,
        "message": f"Assignment {aid} updated successfully.",
        "assignment": priority.enrich_assignment(updated) if updated else {},
    }


def tool_mark_assignment_completed(
    assignment_id: int,
    db_path: str = database.DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Mark an assignment as completed and automatically cancel future scheduled email reminders for it."""
    try:
        aid = int(assignment_id)
    except (ValueError, TypeError):
        return {"success": False, "error": "assignment_id must be an integer."}

    existing = database.get_assignment_by_id(aid, db_path=db_path)
    if not existing:
        return {"success": False, "error": f"Assignment with ID {aid} does not exist."}

    success = database.mark_assignment_completed(aid, db_path=db_path)
    if success:
        return {
            "success": True,
            "message": f"Assignment {aid} ('{existing['title']}') marked as completed. Scheduled reminders cancelled.",
        }
    return {"success": False, "error": f"Could not update status for assignment {aid}."}


def tool_delete_assignment(
    assignment_id: int,
    confirmed: bool = False,
    db_path: str = database.DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Safely delete an assignment. Destructive action requires user confirmation.
    """
    try:
        aid = int(assignment_id)
    except (ValueError, TypeError):
        return {"success": False, "error": "assignment_id must be an integer."}

    existing = database.get_assignment_by_id(aid, db_path=db_path)
    if not existing:
        return {"success": False, "error": f"Assignment with ID {aid} not found."}

    if not confirmed:
        return {
            "success": False,
            "requires_confirmation": True,
            "message": (
                f"Destructive action: Deleting assignment {aid} ('{existing['title']}') cannot be undone. "
                f"Please ask the user to explicitly confirm before proceeding."
            ),
        }

    success = database.delete_assignment(aid, db_path=db_path)
    if success:
        return {
            "success": True,
            "message": f"Assignment {aid} ('{existing['title']}') has been permanently deleted from SQLite.",
        }
    return {"success": False, "error": f"Failed to delete assignment {aid}."}


def tool_generate_daily_plan(
    available_hours: float = 8.0,
    db_path: str = database.DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Generate an intelligent daily task plan using real pending assignments from SQLite.
    Never includes completed assignments.
    Categorizes tasks into overdue/critical, high priority for today, and upcoming.
    """
    try:
        hours_limit = float(available_hours)
        if hours_limit <= 0:
            hours_limit = 8.0
    except (ValueError, TypeError):
        hours_limit = 8.0

    raw_all = database.get_assignments(db_path=db_path)
    # Strictly exclude completed assignments
    raw_pending = [a for a in raw_all if str(a.get("status", "")).strip().lower() != "completed"]

    if not raw_pending:
        return {
            "success": True,
            "message": "No pending assignments found. All caught up!",
            "overdue_tasks": [],
            "planned_tasks": [],
            "total_planned_effort": 0.0,
            "recommended_next_action": "Take a well-deserved break or review upcoming course materials.",
        }

    sorted_tasks = priority.sort_assignments_by_priority(raw_pending)

    overdue_tasks = [t for t in sorted_tasks if t.get("is_overdue") or t.get("urgency", "").upper() == "CRITICAL"]
    active_tasks = [t for t in sorted_tasks if not (t.get("is_overdue") or t.get("urgency", "").upper() == "CRITICAL")]

    planned = []
    current_effort = 0.0

    # Overdue tasks are mandatory in the daily plan
    for task in overdue_tasks:
        effort = task.get("estimated_effort", 1.0)
        planned.append(task)
        current_effort += effort

    # Add upcoming tasks up to available hours limit
    for task in active_tasks:
        effort = task.get("estimated_effort", 1.0)
        if current_effort + effort <= hours_limit or len(planned) == 0:
            planned.append(task)
            current_effort += effort
        elif task.get("urgency", "").upper() in ["HIGH", "CRITICAL"]:
            planned.append(task)
            current_effort += effort

    top_recommendation = priority.get_recommended_next_task(sorted_tasks)

    return {
        "success": True,
        "total_pending_count": len(sorted_tasks),
        "overdue_count": len(overdue_tasks),
        "overdue_tasks": overdue_tasks,
        "planned_tasks": planned,
        "total_planned_effort_hours": round(current_effort, 1),
        "recommended_next_action": (
            f"Focus on '{top_recommendation['title']}' ({top_recommendation['subject']}). "
            f"{top_recommendation.get('recommendation_reason', '')}"
            if top_recommendation
            else "None"
        ),
    }


def tool_get_upcoming_deadlines(
    hours_ahead: int = 72,
    db_path: str = database.DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """Retrieve pending assignments due within the next N hours."""
    try:
        hours = int(hours_ahead)
        if hours <= 0:
            hours = 72
    except (ValueError, TypeError):
        hours = 72

    raw_all = database.get_assignments(db_path=db_path)
    raw_pending = [a for a in raw_all if str(a.get("status", "")).strip().lower() != "completed"]
    now_dt = priority.get_current_time()

    upcoming = []
    for a in raw_pending:
        enriched = priority.enrich_assignment(a, now_dt=now_dt)
        try:
            dl_val = a.get("deadline") or f"{a.get('deadline_date')}T{a.get('deadline_time')}:00"
            deadline_dt = priority.parse_deadline(str(dl_val))
            diff_hours = (deadline_dt - now_dt).total_seconds() / 3600.0
            if diff_hours <= hours:
                enriched["hours_until_deadline"] = round(diff_hours, 1)
                upcoming.append(enriched)
        except Exception:
            continue

    sorted_upcoming = sorted(upcoming, key=lambda x: x["priority_score"], reverse=True)

    return {
        "success": True,
        "hours_window": hours,
        "count": len(sorted_upcoming),
        "upcoming_assignments": sorted_upcoming,
    }


def tool_create_reminder_schedule(
    assignment_id: int,
    recipient: Optional[str] = None,
    db_path: str = database.DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Create automated 24-hour and 3-hour reminder schedule records for an assignment in SQLite.
    Prevents duplicates if reminders have already been sent or scheduled.
    """
    try:
        aid = int(assignment_id)
    except (ValueError, TypeError):
        return {"success": False, "error": "assignment_id must be an integer."}

    assignment = database.get_assignment_by_id(aid, db_path=db_path)
    if not assignment:
        return {"success": False, "error": f"Assignment with ID {aid} not found in database."}

    if str(assignment.get("status", "")).strip().lower() == "completed":
        return {"success": False, "error": f"Assignment {aid} is already completed. No reminders needed."}

    dl_val = assignment.get("deadline") or f"{assignment.get('deadline_date')}T{assignment.get('deadline_time')}:00"
    deadline_dt = priority.parse_deadline(str(dl_val))

    recipient_email = (recipient or "").strip()
    if not recipient_email:
        import os
        recipient_email = os.getenv("NOTIFICATION_RECIPIENT_EMAIL", "").strip() or "student@example.com"

    scheduled = []
    now_dt = priority.get_current_time()

    # 1. 24h reminder
    t_24h = deadline_dt.timestamp() - (24 * 3600)
    dt_24h = datetime.fromtimestamp(t_24h, tz=deadline_dt.tzinfo)
    if dt_24h > now_dt and not database.has_reminder_been_sent(aid, "24h_before", db_path=db_path):
        rid = database.create_reminder_record(
            assignment_id=aid,
            reminder_type="24h_before",
            scheduled_time=dt_24h.isoformat(),
            delivery_status="SCHEDULED",
            recipient=recipient_email,
            db_path=db_path,
        )
        scheduled.append({"id": rid, "type": "24h_before", "time": dt_24h.isoformat()})

    # 2. 3h reminder
    t_3h = deadline_dt.timestamp() - (3 * 3600)
    dt_3h = datetime.fromtimestamp(t_3h, tz=deadline_dt.tzinfo)
    if dt_3h > now_dt and not database.has_reminder_been_sent(aid, "3h_before", db_path=db_path):
        rid = database.create_reminder_record(
            assignment_id=aid,
            reminder_type="3h_before",
            scheduled_time=dt_3h.isoformat(),
            delivery_status="SCHEDULED",
            recipient=recipient_email,
            db_path=db_path,
        )
        scheduled.append({"id": rid, "type": "3h_before", "time": dt_3h.isoformat()})

    return {
        "success": True,
        "assignment_id": aid,
        "scheduled_count": len(scheduled),
        "scheduled_reminders": scheduled,
        "message": f"Successfully created {len(scheduled)} reminder schedule records for assignment {aid}.",
    }


# Map tool names to execution functions
TOOL_EXECUTORS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "get_assignments": tool_get_assignments,
    "add_assignment": tool_add_assignment,
    "get_assignment_details": tool_get_assignment_details,
    "update_assignment": tool_update_assignment,
    "mark_assignment_completed": tool_mark_assignment_completed,
    "get_upcoming_deadlines": tool_get_upcoming_deadlines,
    "generate_daily_plan": tool_generate_daily_plan,
    "create_reminder_schedule": tool_create_reminder_schedule,
    "delete_assignment": tool_delete_assignment,
}


def execute_tool_call(tool_name: str, arguments: Dict[str, Any], db_path: str = database.DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Execute a registered tool function securely with provided arguments."""
    executor = TOOL_EXECUTORS.get(tool_name)
    if not executor:
        return {
            "success": False,
            "error": f"Tool '{tool_name}' is not recognized or not registered.",
        }

    try:
        kwargs = dict(arguments)
        return executor(**kwargs, db_path=db_path)
    except TypeError as te:
        return {"success": False, "error": f"Invalid arguments for tool '{tool_name}': {str(te)}"}
    except Exception as exc:
        return {"success": False, "error": f"Error executing tool '{tool_name}': {str(exc)}"}


# Tool Declarations Schema for Google Gen AI SDK
TOOL_DECLARATIONS = [
    {
        "name": "get_assignments",
        "description": "Retrieve assignments from the SQLite database with optional filters for status (Pending/Completed), subject, or search keywords. Returns assignments enriched with urgency, remaining time, and deterministic priority score.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "status": {"type": "STRING", "description": "Filter by status: 'Pending', 'Completed', or 'All'."},
                "subject": {"type": "STRING", "description": "Filter by course/subject name."},
                "search": {"type": "STRING", "description": "Search keyword for title or description."},
            },
        },
    },
    {
        "name": "add_assignment",
        "description": "Add a new assignment to the database. Validates all inputs and stores them in SQLite.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Title of the assignment."},
                "subject": {"type": "STRING", "description": "Course/Subject name (e.g. CS101, Math)."},
                "deadline": {"type": "STRING", "description": "Deadline in ISO format 'YYYY-MM-DDTHH:MM:SS'."},
                "deadline_date": {"type": "STRING", "description": "Deadline date 'YYYY-MM-DD'."},
                "deadline_time": {"type": "STRING", "description": "Deadline time 'HH:MM'."},
                "description": {"type": "STRING", "description": "Detailed description or notes."},
                "importance": {"type": "STRING", "description": "Importance level: 'Low', 'Medium', 'High', or 'Urgent'."},
                "estimated_effort": {"type": "NUMBER", "description": "Estimated hours needed to complete (e.g. 2.0)."},
            },
            "required": ["title", "subject"],
        },
    },
    {
        "name": "get_assignment_details",
        "description": "Retrieve full details and reminder history for a specific assignment by ID.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "assignment_id": {"type": "INTEGER", "description": "The unique ID of the assignment."},
            },
            "required": ["assignment_id"],
        },
    },
    {
        "name": "update_assignment",
        "description": "Update an existing assignment's attributes (title, subject, deadline, importance, effort, status) in SQLite.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "assignment_id": {"type": "INTEGER", "description": "Assignment ID to update."},
                "title": {"type": "STRING", "description": "New title."},
                "subject": {"type": "STRING", "description": "New subject."},
                "description": {"type": "STRING", "description": "New description."},
                "deadline": {"type": "STRING", "description": "New deadline ISO string."},
                "deadline_date": {"type": "STRING", "description": "New deadline date YYYY-MM-DD."},
                "deadline_time": {"type": "STRING", "description": "New deadline time HH:MM."},
                "importance": {"type": "STRING", "description": "New importance: Low, Medium, High, Urgent."},
                "estimated_effort": {"type": "NUMBER", "description": "New estimated effort in hours."},
                "status": {"type": "STRING", "description": "New status: Pending, In Progress, Completed."},
            },
            "required": ["assignment_id"],
        },
    },
    {
        "name": "mark_assignment_completed",
        "description": "Mark an assignment as completed and automatically cancel pending scheduled reminders.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "assignment_id": {"type": "INTEGER", "description": "The ID of the assignment to complete."},
            },
            "required": ["assignment_id"],
        },
    },
    {
        "name": "get_upcoming_deadlines",
        "description": "Retrieve pending assignments due within the next N hours (default 72h).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "hours_ahead": {"type": "INTEGER", "description": "Hours horizon (e.g. 24, 48, 72)."},
            },
        },
    },
    {
        "name": "generate_daily_plan",
        "description": "Generate a prioritized daily study plan from real pending assignments in SQLite.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "available_hours": {"type": "NUMBER", "description": "Total available study hours today (e.g. 6.0)."},
            },
        },
    },
    {
        "name": "create_reminder_schedule",
        "description": "Schedule 24-hour and 3-hour automated reminders for an assignment in SQLite.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "assignment_id": {"type": "INTEGER", "description": "The assignment ID to schedule reminders for."},
                "recipient": {"type": "STRING", "description": "Recipient email address."},
            },
            "required": ["assignment_id"],
        },
    },
    {
        "name": "delete_assignment",
        "description": "Permanently delete an assignment. DESTRUCTIVE: Requires confirmed=True.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "assignment_id": {"type": "INTEGER", "description": "The assignment ID to delete."},
                "confirmed": {"type": "BOOLEAN", "description": "Must be True only after explicit user confirmation."},
            },
            "required": ["assignment_id"],
        },
    },
]
