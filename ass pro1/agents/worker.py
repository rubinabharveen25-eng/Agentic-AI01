"""
agents/worker.py - Worker Agent for Multi-Agent AI System.
Role: Execute approved assignment-management actions.
Uses Gemini function calling with registered tools to interact with SQLite.
Validates all tool arguments, ensures destructive actions require confirmation,
and tracks full tool execution traces.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import config
import database
import tools

logger = logging.getLogger(__name__)

WORKER_SYSTEM_PROMPT = """You are the Worker Agent in the ASSIGNMENT DEADLINE AGENT multi-agent system.
Your role: Execute approved assignment-management actions accurately and reliably using registered tools.

Core Guidelines:
1. TRUTHFUL OPERATIONS:
   - Always call registered tools to retrieve, add, update, complete, or schedule reminders for assignments.
   - All database changes MUST be performed by Python functions, never fabricated.
   - If a tool returns failure or an error, truthfully report that failure. Never pretend an operation succeeded.
2. ARGUMENT VALIDATION:
   - Provide valid parameters for every tool call (dates as YYYY-MM-DD, times as HH:MM, importance as Low/Medium/High/Urgent).
3. SAFETY & DESTRUCTIVE ACTIONS:
   - Deleting assignments is permanent. Do NOT delete an assignment without explicit confirmation from the user.
   - If user asks to delete an assignment without confirmed=True, state the assignment details and request explicit confirmation.
4. REMINDERS:
   - When requested, schedule 24h and 3h reminders using create_reminder_schedule.
5. REPORTING:
   - Summarize the actions taken and their real outcomes clearly using concise Markdown formatting.
"""


class WorkerAgent:
    """Worker Agent executes assignment management actions using Gemini tool calling."""

    def __init__(self, model_id: Optional[str] = None, api_key: Optional[str] = None):
        self.model_id = (model_id or config.get_worker_model()).strip()
        self.api_key = api_key or config.get_gemini_api_key()

    def execute_task(
        self,
        user_prompt: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        db_path: str = database.DEFAULT_DB_PATH,
        max_turns: int = 5,
    ) -> Dict[str, Any]:
        """
        Execute user request via Gemini function calling loop.
        Calls registered Python tools, feeds results back to Gemini,
        and returns the final response and tool execution traces.
        """
        client = config.get_gemini_client(self.api_key)
        from google.genai import types

        # Prepare tools configuration
        tool_config_obj = types.GenerateContentConfig(
            system_instruction=WORKER_SYSTEM_PROMPT,
            temperature=0.1,
            tools=[{"function_declarations": tools.TOOL_DECLARATIONS}],
        )

        tool_traces: List[Dict[str, Any]] = []

        # Build initial contents
        contents: List[Any] = []
        if conversation_history:
            for msg in conversation_history:
                role = msg.get("role", "user")
                text = msg.get("content", "")
                if role == "user":
                    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))
                elif role == "assistant":
                    contents.append(types.Content(role="model", parts=[types.Part.from_text(text=text)]))

        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)]))

        current_turn = 0
        final_text = ""

        while current_turn < max_turns:
            current_turn += 1

            response = client.models.generate_content(
                model=self.model_id,
                contents=contents,
                config=tool_config_obj,
            )

            # Check if model requested function calls
            function_calls = getattr(response, "function_calls", None)
            if not function_calls:
                # Direct text response without further tool calls
                final_text = getattr(response, "text", "") or ""
                break

            # Append model turn with function calls to contents history
            model_parts = []
            for candidate in getattr(response, "candidates", []):
                for part in getattr(candidate.content, "parts", []):
                    model_parts.append(part)
            if model_parts:
                contents.append(types.Content(role="model", parts=model_parts))

            # Execute tool calls in Python
            tool_response_parts = []
            for fcall in function_calls:
                call_name = fcall.name
                call_args = fcall.args if isinstance(fcall.args, dict) else dict(fcall.args or {})

                logger.info(f"Worker executing tool: {call_name} with args: {call_args}")
                tool_result = tools.execute_tool_call(call_name, call_args, db_path=db_path)

                tool_traces.append({
                    "tool": call_name,
                    "arguments": call_args,
                    "result": tool_result,
                })

                # Format response back to Gemini
                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=call_name,
                        response={"result": tool_result},
                    )
                )

            # Append tool responses as user/function turn
            contents.append(types.Content(role="user", parts=tool_response_parts))

        return {
            "success": True,
            "response": final_text,
            "tool_traces": tool_traces,
            "turns_taken": current_turn,
            "model_used": self.model_id,
        }

    def execute_direct_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        db_path: str = database.DEFAULT_DB_PATH,
    ) -> Dict[str, Any]:
        """Direct execution of an approved tool for programmatic orchestration."""
        return tools.execute_tool_call(tool_name, arguments, db_path=db_path)
