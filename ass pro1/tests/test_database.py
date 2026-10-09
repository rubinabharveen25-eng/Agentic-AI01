"""
test_database.py - Unit tests for database.py SQLite operations.
Verifies CRUD, parameterized queries, filtering, and reminder log management.
"""

import database


def test_init_db(temp_db: str):
    """Verify that tables and indexes are created without error."""
    with database.get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row["name"] for row in cursor.fetchall()]
        assert "assignments" in tables
        assert "reminder_logs" in tables


def test_add_and_get_assignment(temp_db: str):
    """Verify adding an assignment and retrieving it by ID."""
    aid = database.add_assignment(
        title="Physics Lab 2",
        subject="PHYS101",
        deadline_date="2026-10-20",
        deadline_time="23:59",
        description="Optics lab data analysis",
        importance="Urgent",
        estimated_effort=2.5,
        db_path=temp_db,
    )
    assert aid > 0

    item = database.get_assignment_by_id(aid, db_path=temp_db)
    assert item is not None
    assert item["title"] == "Physics Lab 2"
    assert item["subject"] == "PHYS101"
    assert item["importance"] == "Urgent"
    assert item["estimated_effort"] == 2.5
    assert item["status"] == "Pending"
    assert item["completed_at"] is None


def test_get_assignments_filtering(temp_db: str):
    """Verify filtering by status, subject, and search term."""
    database.add_assignment("Essay 1", "ENG101", "2026-10-12", "12:00", db_path=temp_db)
    database.add_assignment("Essay 2", "ENG101", "2026-10-18", "12:00", db_path=temp_db)
    aid3 = database.add_assignment("Algo HW", "CS200", "2026-10-14", "18:00", db_path=temp_db)

    database.mark_assignment_completed(aid3, db_path=temp_db)

    # Filter status
    pending = database.get_assignments(status="Pending", db_path=temp_db)
    assert len(pending) == 2
    assert all(a["status"] == "Pending" for a in pending)

    completed = database.get_assignments(status="Completed", db_path=temp_db)
    assert len(completed) == 1
    assert completed[0]["title"] == "Algo HW"

    # Filter subject
    eng_tasks = database.get_assignments(subject="ENG101", db_path=temp_db)
    assert len(eng_tasks) == 2

    # Filter search
    search_res = database.get_assignments(search="Algo", db_path=temp_db)
    assert len(search_res) == 1
    assert search_res[0]["id"] == aid3


def test_update_assignment(temp_db: str, sample_assignment: int):
    """Verify updating assignment details."""
    updated = database.update_assignment(
        assignment_id=sample_assignment,
        title="Updated Problem Set",
        estimated_effort=4.0,
        importance="Urgent",
        db_path=temp_db,
    )
    assert updated is True

    item = database.get_assignment_by_id(sample_assignment, db_path=temp_db)
    assert item["title"] == "Updated Problem Set"
    assert item["estimated_effort"] == 4.0
    assert item["importance"] == "Urgent"


def test_mark_assignment_completed_and_cancel_reminders(temp_db: str, sample_assignment: int):
    """Verify completing assignment cancels scheduled reminders."""
    # Add a scheduled reminder
    database.log_reminder(
        assignment_id=sample_assignment,
        reminder_type="24h_before",
        scheduled_for="2026-10-14T17:00:00",
        recipient="student@test.edu",
        status="SCHEDULED",
        db_path=temp_db,
    )

    success = database.mark_assignment_completed(sample_assignment, db_path=temp_db)
    assert success is True

    item = database.get_assignment_by_id(sample_assignment, db_path=temp_db)
    assert item["status"] == "Completed"
    assert item["completed_at"] is not None

    logs = database.get_reminder_logs(assignment_id=sample_assignment, db_path=temp_db)
    assert len(logs) == 1
    assert logs[0]["status"] == "CANCELLED"


def test_delete_assignment(temp_db: str, sample_assignment: int):
    """Verify deleting an assignment removes it from SQLite."""
    deleted = database.delete_assignment(sample_assignment, db_path=temp_db)
    assert deleted is True

    item = database.get_assignment_by_id(sample_assignment, db_path=temp_db)
    assert item is None


def test_reminder_logs_crud(temp_db: str, sample_assignment: int):
    """Verify logging, updating, and querying reminder events."""
    log_id = database.log_reminder(
        assignment_id=sample_assignment,
        reminder_type="3h_before",
        scheduled_for="2026-10-15T14:00:00",
        recipient="user@test.edu",
        status="SCHEDULED",
        db_path=temp_db,
    )
    assert log_id > 0

    assert database.has_reminder_been_sent(sample_assignment, "3h_before", db_path=temp_db) is False

    # Mark as sent
    database.update_reminder_status(log_id, status="SENT", sent_at="2026-10-15T14:00:05", db_path=temp_db)
    assert database.has_reminder_been_sent(sample_assignment, "3h_before", db_path=temp_db) is True

    logs = database.get_reminder_logs(db_path=temp_db)
    assert len(logs) == 1
    assert logs[0]["status"] == "SENT"
    assert logs[0]["recipient"] == "user@test.edu"
