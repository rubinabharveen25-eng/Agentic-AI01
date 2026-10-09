"""
test_reminders.py - Unit tests for reminder scheduling, duplicate prevention, and SMTP dispatch.
Mocks SMTP to guarantee zero network calls or credentials exposure.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import smtplib

import pytest

import database
import priority
import reminders


def test_send_email_success(mock_smtp):
    """Verify send_email reports success when SMTP succeeds."""
    success, msg = reminders.send_email("student@test.edu", "Test Subject", "<p>Hello</p>")
    assert success is True
    assert "delivered" in msg.lower()
    assert mock_smtp.sendmail.called


def test_send_email_auth_failure(monkeypatch: pytest.MonkeyPatch):
    """Verify send_email safely returns False when authentication fails."""
    def fake_smtp_auth_fail(*args, **kwargs):
        server = MagicMock()
        server.__enter__.return_value = server
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
        return server

    monkeypatch.setattr("smtplib.SMTP", fake_smtp_auth_fail)
    monkeypatch.setenv("SMTP_EMAIL", "test@test.edu")
    monkeypatch.setenv("SMTP_PASSWORD", "bad_pass")

    success, msg = reminders.send_email("student@test.edu", "Subject", "Body")
    assert success is False
    assert "Authentication failed" in msg


def test_send_email_connection_failure(monkeypatch: pytest.MonkeyPatch):
    """Verify send_email safely returns False when SMTP server cannot be reached."""
    def fake_smtp_conn_fail(*args, **kwargs):
        raise smtplib.SMTPConnectError(421, "Cannot connect to server")

    monkeypatch.setattr("smtplib.SMTP", fake_smtp_conn_fail)
    monkeypatch.setenv("SMTP_EMAIL", "test@test.edu")
    monkeypatch.setenv("SMTP_PASSWORD", "pass")

    success, msg = reminders.send_email("student@test.edu", "Subject", "Body")
    assert success is False
    assert "Connection failed" in msg


def test_process_scheduled_reminders_milestones_and_duplicate_prevention(temp_db: str, mock_smtp, monkeypatch: pytest.MonkeyPatch):
    """Verify 24h, 3h, and overdue reminders are triggered and duplicate notifications are prevented."""
    now_dt = priority.get_current_time()
    today_date = now_dt.date()

    # 1. Assignment due in 12 hours (should trigger 24h_before)
    dt_24h = now_dt + timedelta(hours=12)
    aid_24h = database.add_assignment(
        "Essay 24h", "ENG", dt_24h.strftime("%Y-%m-%d"), dt_24h.strftime("%H:%M"), db_path=temp_db
    )

    # 2. Assignment due in 2 hours (should trigger 3h_before)
    dt_3h = now_dt + timedelta(hours=2)
    aid_3h = database.add_assignment(
        "Quiz 3h", "MATH", dt_3h.strftime("%Y-%m-%d"), dt_3h.strftime("%H:%M"), db_path=temp_db
    )

    # 3. Assignment overdue by 2 hours (should trigger overdue)
    dt_overdue = now_dt - timedelta(hours=2)
    aid_overdue = database.add_assignment(
        "Lab Overdue", "PHYS", dt_overdue.strftime("%Y-%m-%d"), dt_overdue.strftime("%H:%M"), db_path=temp_db
    )

    # 4. Assignment completed (should be ignored)
    aid_completed = database.add_assignment(
        "Done HW", "CS", dt_3h.strftime("%Y-%m-%d"), dt_3h.strftime("%H:%M"), db_path=temp_db
    )
    database.mark_assignment_completed(aid_completed, db_path=temp_db)

    # Run reminder processor FIRST time
    processed_count_1 = reminders.process_scheduled_reminders(db_path=temp_db)
    assert processed_count_1 == 3  # aid_24h, aid_3h, aid_overdue

    # Check reminder logs in DB
    logs = database.get_reminder_logs(db_path=temp_db)
    assert len(logs) == 3
    for log in logs:
        assert log["status"] == "SENT"

    # Run reminder processor SECOND time - MUST NOT SEND DUPLICATES!
    mock_smtp.sendmail.reset_mock()
    processed_count_2 = reminders.process_scheduled_reminders(db_path=temp_db)
    assert processed_count_2 == 0
    assert not mock_smtp.sendmail.called


def test_send_test_email(temp_db: str, mock_smtp):
    """Verify test email is dispatched and recorded in SQLite."""
    # Seed an assignment so test email can log
    database.add_assignment("Seed Task", "SEED", "2026-10-30", "12:00", db_path=temp_db)

    success, msg = reminders.send_test_email(target_email="testuser@example.com", db_path=temp_db)
    assert success is True
    assert "delivered" in msg.lower()

    logs = database.get_reminder_logs(db_path=temp_db)
    test_logs = [l for l in logs if l["reminder_type"] == "test_email"]
    assert len(test_logs) == 1
    assert test_logs[0]["status"] == "SENT"
