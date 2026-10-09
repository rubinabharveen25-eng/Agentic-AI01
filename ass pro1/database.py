"""
database.py - SQLite Database persistence layer for Assignment Deadline Agent.
Uses parameterized queries to prevent SQL injection and ensures data persistence.
Supports timezone-aware date and time handling.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "assignments.db")


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create and return a SQLite database connection with Row factory enabled."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _format_deadline(deadline_str: str) -> tuple[str, str, str]:
    """
    Given a deadline string (either ISO format 'YYYY-MM-DDTHH:MM:SS' or date 'YYYY-MM-DD' or with time),
    returns (iso_deadline, date_str, time_str).
    """
    cleaned = deadline_str.strip()
    if "T" in cleaned:
        parts = cleaned.split("T", 1)
        d_part = parts[0]
        t_part = parts[1][:5]
        return cleaned, d_part, t_part
    elif " " in cleaned:
        parts = cleaned.split(" ", 1)
        d_part = parts[0]
        t_part = parts[1][:5]
        return f"{d_part}T{t_part}:00", d_part, t_part
    else:
        # Just a date
        return f"{cleaned}T23:59:00", cleaned, "23:59"


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize the SQLite database and create necessary tables if they do not exist."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Assignments table matching exact specification:
        # id, title, subject, description, deadline, importance, estimated_effort, status, created_at, updated_at
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                subject TEXT NOT NULL,
                description TEXT DEFAULT '',
                deadline TEXT NOT NULL,
                deadline_date TEXT,
                deadline_time TEXT,
                importance TEXT NOT NULL DEFAULT 'Medium',
                estimated_effort REAL NOT NULL DEFAULT 1.0,
                status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )

        # Reminder records table matching exact specification:
        # id, assignment_id, reminder_type, scheduled_time, delivery_status, sent_at
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS reminder_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER NOT NULL,
                reminder_type TEXT NOT NULL,
                scheduled_time TEXT NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'SCHEDULED',
                sent_at TEXT,
                recipient TEXT DEFAULT '',
                error_message TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (assignment_id) REFERENCES assignments (id) ON DELETE CASCADE
            )
            """
        )

        # Reminder logs table for test and legacy compatibility
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS reminder_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER NOT NULL,
                reminder_type TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                recipient TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'SCHEDULED',
                sent_at TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (assignment_id) REFERENCES assignments (id) ON DELETE CASCADE
            )
            """
        )

        # Create indexes for fast querying
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_assignments_status ON assignments(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_assignments_deadline ON assignments(deadline)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_assignment ON reminder_records(assignment_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminder_records(delivery_status)")
        conn.commit()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a SQLite Row to a dictionary and populate deadline_date and deadline_time if missing."""
    d = dict(row)
    if "deadline" in d and d["deadline"]:
        iso, date_str, time_str = _format_deadline(str(d["deadline"]))
        if "deadline_date" not in d or not d["deadline_date"]:
            d["deadline_date"] = date_str
        if "deadline_time" not in d or not d["deadline_time"]:
            d["deadline_time"] = time_str
    elif "deadline_date" in d and d["deadline_date"]:
        time_part = d.get("deadline_time") or "23:59"
        d["deadline"] = f"{d['deadline_date']}T{time_part}:00"
    return d


def add_assignment(
    title: str,
    subject: str,
    deadline_date: Optional[str] = None,
    deadline_time: Optional[str] = None,
    description: str = "",
    importance: str = "Medium",
    estimated_effort: float = 1.0,
    deadline: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> int:
    """
    Insert a new assignment into the database using parameterized queries.
    Returns the newly inserted assignment's ID.
    Accepts either (deadline_date, deadline_time) or a combined deadline ISO string.
    """
    now_utc = datetime.now(timezone.utc).isoformat()

    if deadline:
        iso_deadline, d_date, d_time = _format_deadline(deadline)
    else:
        d_date = (deadline_date or "").strip()
        d_time = (deadline_time or "23:59").strip()
        # Normalize time
        parts = d_time.split(":")
        if len(parts) >= 2:
            d_time = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
        iso_deadline = f"{d_date}T{d_time}:00"

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO assignments (
                title, subject, description, deadline, deadline_date, deadline_time,
                importance, estimated_effort, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?)
            """,
            (
                title.strip(),
                subject.strip(),
                description.strip() if description else "",
                iso_deadline,
                d_date,
                d_time,
                importance.strip(),
                float(estimated_effort),
                now_utc,
                now_utc,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_assignments(
    status: Optional[str] = None,
    subject: Optional[str] = None,
    search: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """Retrieve assignments with optional filters."""
    query = "SELECT * FROM assignments WHERE 1=1"
    params: List[Any] = []

    if status and status != "All":
        query += " AND status = ?"
        params.append(status)

    if subject and subject != "All":
        query += " AND LOWER(subject) = LOWER(?)"
        params.append(subject)

    if search:
        query += " AND (title LIKE ? OR description LIKE ? OR subject LIKE ?)"
        wildcard = f"%{search}%"
        params.extend([wildcard, wildcard, wildcard])

    query += " ORDER BY deadline ASC, id ASC"

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [_row_to_dict(row) for row in rows]


def get_assignment_by_id(assignment_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieve a single assignment by its ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,))
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None


def update_assignment(
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
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """Update fields of an assignment. Returns True if updated, False if assignment not found."""
    fields = []
    params: List[Any] = []
    now_utc = datetime.now(timezone.utc).isoformat()

    if title is not None:
        fields.append("title = ?")
        params.append(title.strip())
    if subject is not None:
        fields.append("subject = ?")
        params.append(subject.strip())
    if description is not None:
        fields.append("description = ?")
        params.append(description.strip())

    if deadline is not None:
        iso_dl, d_date, d_time = _format_deadline(deadline)
        fields.append("deadline = ?")
        params.append(iso_dl)
        fields.append("deadline_date = ?")
        params.append(d_date)
        fields.append("deadline_time = ?")
        params.append(d_time)
    else:
        if deadline_date is not None:
            fields.append("deadline_date = ?")
            params.append(deadline_date.strip())
        if deadline_time is not None:
            fields.append("deadline_time = ?")
            params.append(deadline_time.strip())
        if deadline_date is not None or deadline_time is not None:
            # Refresh combined deadline column
            existing = get_assignment_by_id(assignment_id, db_path=db_path)
            if existing:
                d_date = deadline_date.strip() if deadline_date is not None else existing.get("deadline_date", "")
                d_time = deadline_time.strip() if deadline_time is not None else existing.get("deadline_time", "23:59")
                fields.append("deadline = ?")
                params.append(f"{d_date}T{d_time}:00")

    if importance is not None:
        fields.append("importance = ?")
        params.append(importance.strip())
    if estimated_effort is not None:
        fields.append("estimated_effort = ?")
        params.append(float(estimated_effort))
    if status is not None:
        clean_status = status.strip()
        fields.append("status = ?")
        params.append(clean_status)
        if clean_status.lower() == "completed":
            fields.append("completed_at = ?")
            params.append(now_utc)
        elif clean_status.lower() == "pending":
            fields.append("completed_at = NULL")

    if not fields:
        return False

    fields.append("updated_at = ?")
    params.append(now_utc)

    params.append(assignment_id)
    query = f"UPDATE assignments SET {', '.join(fields)} WHERE id = ?"

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.rowcount > 0


def mark_assignment_completed(assignment_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Mark an assignment as completed, set completed_at and updated_at timestamps, cancel pending reminders."""
    now_utc = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE assignments
            SET status = 'Completed', completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now_utc, now_utc, assignment_id),
        )
        conn.commit()
        if cursor.rowcount > 0:
            cancel_pending_reminders_for_assignment(assignment_id, db_path=db_path)
            return True
        return False


def delete_assignment(assignment_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Delete an assignment and associated reminders from SQLite."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        conn.commit()
        return cursor.rowcount > 0


# --- Reminder Records Management ---

def create_reminder_record(
    assignment_id: int,
    reminder_type: str,
    scheduled_time: str,
    delivery_status: str = "SCHEDULED",
    recipient: str = "",
    sent_at: Optional[str] = None,
    error_message: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> int:
    """Create a new reminder record."""
    now_utc = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO reminder_records (
                assignment_id, reminder_type, scheduled_time, delivery_status,
                sent_at, recipient, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assignment_id,
                reminder_type,
                scheduled_time,
                delivery_status,
                sent_at,
                recipient,
                error_message,
                now_utc,
            ),
        )
        rid = int(cursor.lastrowid)
        # Also populate reminder_logs for backwards compatibility
        cursor.execute(
            """
            INSERT INTO reminder_logs (
                id, assignment_id, reminder_type, scheduled_for, recipient,
                status, sent_at, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rid,
                assignment_id,
                reminder_type,
                scheduled_time,
                recipient,
                delivery_status,
                sent_at,
                error_message,
                now_utc,
            ),
        )
        conn.commit()
        return rid


def log_reminder(
    assignment_id: int,
    reminder_type: str,
    scheduled_for: str,
    recipient: str,
    status: str = "SCHEDULED",
    sent_at: Optional[str] = None,
    error_message: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> int:
    """Compatibility alias for create_reminder_record."""
    return create_reminder_record(
        assignment_id=assignment_id,
        reminder_type=reminder_type,
        scheduled_time=scheduled_for,
        delivery_status=status,
        recipient=recipient,
        sent_at=sent_at,
        error_message=error_message,
        db_path=db_path,
    )


def update_reminder_status(
    reminder_id: int,
    status: str,
    sent_at: Optional[str] = None,
    error_message: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """Update delivery_status, sent_at, or error_message of a reminder record."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE reminder_records
            SET delivery_status = ?, sent_at = ?, error_message = ?
            WHERE id = ?
            """,
            (status, sent_at, error_message, reminder_id),
        )
        cursor.execute(
            """
            UPDATE reminder_logs
            SET status = ?, sent_at = ?, error_message = ?
            WHERE id = ?
            """,
            (status, sent_at, error_message, reminder_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def has_reminder_been_sent(
    assignment_id: int,
    reminder_type: str,
    db_path: str = DEFAULT_DB_PATH,
) -> bool:
    """Check if a reminder of the given type has already been successfully sent for an assignment."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM reminder_records
            WHERE assignment_id = ? AND reminder_type = ? AND delivery_status = 'SENT'
            LIMIT 1
            """,
            (assignment_id, reminder_type),
        )
        return cursor.fetchone() is not None


def cancel_pending_reminders_for_assignment(
    assignment_id: int,
    db_path: str = DEFAULT_DB_PATH,
) -> int:
    """Cancel all SCHEDULED reminders for an assignment (e.g. when completed)."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE reminder_records
            SET delivery_status = 'CANCELLED'
            WHERE assignment_id = ? AND delivery_status = 'SCHEDULED'
            """,
            (assignment_id,),
        )
        cursor.execute(
            """
            UPDATE reminder_logs
            SET status = 'CANCELLED'
            WHERE assignment_id = ? AND status = 'SCHEDULED'
            """,
            (assignment_id,),
        )
        conn.commit()
        return cursor.rowcount


def get_reminder_records(
    assignment_id: Optional[int] = None,
    limit: int = 100,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """Retrieve reminder records with optional assignment filtering."""
    query = """
        SELECT r.*, r.delivery_status AS status, a.title AS assignment_title, a.subject AS assignment_subject
        FROM reminder_records r
        LEFT JOIN assignments a ON r.assignment_id = a.id
        WHERE 1=1
    """
    params: List[Any] = []
    if assignment_id is not None:
        query += " AND r.assignment_id = ?"
        params.append(assignment_id)

    query += " ORDER BY r.id DESC LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_reminder_logs(
    assignment_id: Optional[int] = None,
    limit: int = 100,
    db_path: str = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """Compatibility alias for get_reminder_records."""
    return get_reminder_records(assignment_id=assignment_id, limit=limit, db_path=db_path)
