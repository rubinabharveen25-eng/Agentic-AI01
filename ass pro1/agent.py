"""
agent.py - Compatibility module for Assignment Deadline Agent.
Re-exports agents from the agents package and config module.
"""

from __future__ import annotations

import config
from agents.planner import PlannerAgent
from agents.worker import WorkerAgent
from agents.reviewer import ReviewerAgent
from agents.orchestrator import Orchestrator, MultiAgentWorkflowResult

test_api_connection = config.test_api_connection
get_gemini_client = config.get_gemini_client

__all__ = [
    "PlannerAgent",
    "WorkerAgent",
    "ReviewerAgent",
    "Orchestrator",
    "MultiAgentWorkflowResult",
    "test_api_connection",
    "get_gemini_client",
]
