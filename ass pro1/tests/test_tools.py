"""
test_tools.py - Unit tests for registered agent tools in tools.py.
Validates argument checks, edge cases, safety confirmation, and database execution.
"""

from datetime import date, timedelta
import tools
import database


def test_tool_add_assignment_validation(temp_db: str):
    """Verify strict validation on tool_add_assignment arguments."""
    # 1. Empty title
    res1 = tools.tool_add_assignment(
        title="", subject="MATH", deadline_date="2026-10-15", deadline_time="10:00", db_path=temp_db
    )
    assert res1["success"] is False
    assert "Title" in res1["error"]

    # 2. Invalid date format
    res2 = tools.tool_add_assignment(
        title="Valid Title", subject="MATH", deadline_date="15-10-2026", deadline_time="10:00", db_path=temp_db
    )
    assert res2["success"] is False
    assert "deadline_date" in res2["error"]

    # 3. Invalid time format
    res3 = tools.tool_add_assignment(
        title="Valid Title", subject="MATH", deadline_date="2026-10-15", deadline_time="25:99", db_path=temp_db
    )
    assert res3["success"] is False
    assert "deadline_time" in res3["error"]

    # 4. Invalid importance
    res4 = tools.tool_add_assignment(
        title="Valid Title", subject="MATH", deadline_date="2026-10-15", deadline_time="10:00",
        importance="SuperMegaImportant", db_path=temp_db
    )
    assert res4["success"] is False
    assert "importance" in res4["error"]

    # 5. Invalid effort <= 0
    res5 = tools.tool_add_assignment(
        title="Valid Title", subject="MATH", deadline_date="2026-10-15", deadline_time="10:00",
        estimated_effort=0.0, db_path=temp_db
    )
    assert res5["success"] is False
    assert "estimated_effort" in res5["error"]

    # 6. Valid insertion
    res6 = tools.tool_add_assignment(
        title="Valid Title", subject="MATH", deadline_date="2026-10-15", deadline_time="10:00",
        importance="High", estimated_effort=2.0, db_path=temp_db
    )
    assert res6["success"] is True
    assert "assignment_id" in res6


def test_tool_get_assignment_details(temp_db: str, sample_assignment: int):
    """Verify details retrieval and non-existent ID handling."""
    res_valid = tools.tool_get_assignment_details(sample_assignment, db_path=temp_db)
    assert res_valid["success"] is True
    assert res_valid["assignment"]["id"] == sample_assignment

    res_invalid = tools.tool_get_assignment_details(99999, db_path=temp_db)
    assert res_invalid["success"] is False
    assert "does not exist" in res_invalid["error"]


def test_tool_update_assignment(temp_db: str, sample_assignment: int):
    """Verify tool_update_assignment modifies database record."""
    res = tools.tool_update_assignment(
        assignment_id=sample_assignment,
        title="Updated Problem Set Title",
        importance="Urgent",
        db_path=temp_db,
    )
    assert res["success"] is True
    assert res["assignment"]["title"] == "Updated Problem Set Title"
    assert res["assignment"]["importance"] == "Urgent"


def test_tool_mark_assignment_completed(temp_db: str, sample_assignment: int):
    """Verify marking assignment completed via tool."""
    res = tools.tool_mark_assignment_completed(sample_assignment, db_path=temp_db)
    assert res["success"] is True

    record = database.get_assignment_by_id(sample_assignment, db_path=temp_db)
    assert record["status"] == "Completed"


def test_tool_delete_assignment_safety_confirmation(temp_db: str, sample_assignment: int):
    """Verify destructive delete action requires confirmed=True."""
    # Attempt without confirmed=True
    res_unconfirmed = tools.tool_delete_assignment(sample_assignment, confirmed=False, db_path=temp_db)
    assert res_unconfirmed["success"] is False
    assert res_unconfirmed.get("requires_confirmation") is True

    # Confirm delete
    res_confirmed = tools.tool_delete_assignment(sample_assignment, confirmed=True, db_path=temp_db)
    assert res_confirmed["success"] is True
    assert database.get_assignment_by_id(sample_assignment, db_path=temp_db) is None


def test_tool_generate_daily_plan(temp_db: str):
    """Verify daily plan separates overdue tasks and excludes completed assignments."""
    today_str = date.today().strftime("%Y-%m-%d")
    yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Add overdue task
    aid_overdue = database.add_assignment("Past Task", "HIST", yesterday_str, "12:00", db_path=temp_db)
    # Add active pending task
    aid_active = database.add_assignment("Active Task", "CS", today_str, "23:59", db_path=temp_db)
    # Add completed task
    aid_done = database.add_assignment("Done Task", "ENG", yesterday_str, "10:00", db_path=temp_db)
    database.mark_assignment_completed(aid_done, db_path=temp_db)

    plan = tools.tool_generate_daily_plan(available_hours=6.0, db_path=temp_db)
    assert plan["success"] is True

    # Completed tasks must never appear
    all_plan_ids = [t["id"] for t in plan["planned_tasks"]]
    assert aid_done not in all_plan_ids

    # Overdue tasks must be flagged
    overdue_ids = [t["id"] for t in plan["overdue_tasks"]]
    assert aid_overdue in overdue_ids


def test_execute_tool_call_dispatch(temp_db: str):
    """Verify execute_tool_call dispatching and unknown tool handling."""
    # Unknown tool
    unknown = tools.execute_tool_call("fake_tool", {}, db_path=temp_db)
    assert unknown["success"] is False
    assert "not recognized" in unknown["error"]

    # Known tool
    known = tools.execute_tool_call("get_assignments", {"status": "Pending"}, db_path=temp_db)
    assert known["success"] is True
