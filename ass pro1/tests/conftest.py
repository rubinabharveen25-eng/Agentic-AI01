"""
conftest.py - Pytest fixtures for Assignment Deadline Agent tests.
Provides isolated temporary databases, fixed timestamps, and mock objects.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from typing import Generator
from unittest.mock import MagicMock

import pytest

import database
import reminders


@pytest.fixture
def temp_db() -> Generator[str, None, None]:
    """Provide a temporary SQLite database for test isolation."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database.init_db(db_path=path)
    yield path
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


@pytest.fixture
def sample_assignment(temp_db: str) -> int:
    """Insert a sample pending assignment for tests."""
    return database.add_assignment(
        title="Calculus Problem Set 4",
        subject="MATH201",
        deadline_date="2026-10-15",
        deadline_time="17:00",
        description="Chapters 4 and 5 integrals",
        importance="High",
        estimated_effort=3.0,
        db_path=temp_db,
    )


@pytest.fixture
def mock_smtp(monkeypatch: pytest.MonkeyPatch):
    """Mock SMTP and SMTP_SSL to prevent external network calls during tests."""
    mock_server = MagicMock()
    mock_server.__enter__.return_value = mock_server

    def fake_smtp(*args, **kwargs):
        return mock_server

    monkeypatch.setattr("smtplib.SMTP", fake_smtp)
    monkeypatch.setattr("smtplib.SMTP_SSL", fake_smtp)
    monkeypatch.setenv("SMTP_SERVER", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_EMAIL", "agent@test.edu")
    monkeypatch.setenv("SMTP_PASSWORD", "secret123")
    monkeypatch.setenv("NOTIFICATION_RECIPIENT_EMAIL", "student@test.edu")

    return mock_server
