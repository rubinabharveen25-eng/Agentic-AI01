"""
test_agent.py - Unit tests for Worker Agent tool calling and execution loop.
Uses mocked Google Gen AI SDK calls to ensure tests never spend API credits or make network calls.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

import config
import database
from agents.worker import WorkerAgent


def test_worker_direct_response(monkeypatch: pytest.MonkeyPatch, temp_db: str):
    """Verify worker agent handles direct text response without tool calls."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.function_calls = None
    mock_resp.text = "Hello! I can help you manage your assignments."
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(config, "get_gemini_client", lambda key: mock_client)

    worker = WorkerAgent(api_key="mock_key")
    res = worker.execute_task(user_prompt="Hello", db_path=temp_db)

    assert res["success"] is True
    assert "Hello!" in res["response"]
    assert len(res["tool_traces"]) == 0


def test_worker_tool_calling_execution(monkeypatch: pytest.MonkeyPatch, temp_db: str):
    """
    Verify worker agent detects Gemini function call, executes real Python tool against SQLite,
    returns result back to Gemini, and captures tool traces.
    """
    # Seed an assignment
    database.add_assignment("Math Homework", "MATH", "2026-10-15", "10:00", db_path=temp_db)

    # Mock turn 1 (model requests tool call get_assignments) and turn 2 (model returns final text)
    mock_client = MagicMock()

    turn1_call = MagicMock()
    turn1_call.name = "get_assignments"
    turn1_call.args = {"status": "Pending"}

    turn1_resp = MagicMock()
    turn1_resp.function_calls = [turn1_call]
    turn1_resp.candidates = []

    turn2_resp = MagicMock()
    turn2_resp.function_calls = None
    turn2_resp.text = "You have 1 pending assignment: Math Homework."

    mock_client.models.generate_content.side_effect = [turn1_resp, turn2_resp]
    monkeypatch.setattr(config, "get_gemini_client", lambda key: mock_client)

    worker = WorkerAgent(api_key="mock_key")
    res = worker.execute_task(user_prompt="What are my pending tasks?", db_path=temp_db)

    assert res["success"] is True
    assert "Math Homework" in res["response"]
    assert len(res["tool_traces"]) == 1
    assert res["tool_traces"][0]["tool"] == "get_assignments"
    assert res["tool_traces"][0]["result"]["count"] == 1
