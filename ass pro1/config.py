"""
config.py - Configuration and Google Gen AI SDK initialization.
Loads environment variables securely using python-dotenv.
Configures separate Gemini model IDs for Planner, Worker, and Reviewer.
Provides a real Gemini API connection test.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv

# Load .env file
load_dotenv()

logger = logging.getLogger(__name__)

# Default model IDs (configurable independently via environment variables)
DEFAULT_PLANNER_MODEL = os.getenv("PLANNER_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
DEFAULT_WORKER_MODEL = os.getenv("WORKER_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
DEFAULT_REVIEWER_MODEL = os.getenv("REVIEWER_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

# Fallback/General Gemini Model
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

# Database & Timezone defaults
DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "assignments.db").strip() or "assignments.db"
DEFAULT_APP_TIMEZONE = os.getenv("APP_TIMEZONE", "UTC").strip() or "UTC"


def get_gemini_api_key() -> str:
    """Retrieve Gemini API key from environment."""
    return os.getenv("GEMINI_API_KEY", "").strip()


def get_planner_model() -> str:
    """Retrieve Planner model ID from environment or default."""
    return os.getenv("PLANNER_MODEL", DEFAULT_PLANNER_MODEL).strip() or DEFAULT_PLANNER_MODEL


def get_worker_model() -> str:
    """Retrieve Worker model ID from environment or default."""
    return os.getenv("WORKER_MODEL", DEFAULT_WORKER_MODEL).strip() or DEFAULT_WORKER_MODEL


def get_reviewer_model() -> str:
    """Retrieve Reviewer model ID from environment or default."""
    return os.getenv("REVIEWER_MODEL", DEFAULT_REVIEWER_MODEL).strip() or DEFAULT_REVIEWER_MODEL


def get_gemini_client(api_key: Optional[str] = None):
    """
    Instantiate and return the official Google Gen AI SDK client.
    Raises ValueError if API key is missing or placeholder.
    """
    key = (api_key or get_gemini_api_key()).strip()
    if not key or key == "your_actual_key_here" or key == "your_gemini_api_key_here":
        raise ValueError("GEMINI_API_KEY is not configured. Please set a valid key in .env or the Settings panel.")

    try:
        from google import genai
        return genai.Client(api_key=key)
    except ImportError:
        raise ImportError("google-genai package is not installed. Please run 'pip install google-genai'.")


def test_api_connection(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Perform a real test request against Google Gemini API to verify connectivity,
    valid credentials, and model availability.
    Returns (success: bool, message: str, diagnostic_info: dict).
    Never claims success unless the API call actually succeeds.
    """
    key = (api_key if api_key is not None else get_gemini_api_key()).strip()
    if not key or key in ("your_actual_key_here", "your_gemini_api_key_here"):
        return (
            False,
            "GEMINI_API_KEY is missing or contains placeholder text. Please configure a valid key in .env or Settings.",
            {"error_type": "MissingKey"},
        )

    target_model = (model or get_planner_model()).strip()

    try:
        client = get_gemini_client(key)
        # Issue a minimal real generation call to test model availability and API key validity
        response = client.models.generate_content(
            model=target_model,
            contents="Respond with only the single word: OK",
        )

        resp_text = getattr(response, "text", "") or ""
        return (
            True,
            f"Successfully connected to Gemini API using model '{target_model}'.",
            {
                "model": target_model,
                "response_sample": resp_text.strip()[:50],
            },
        )
    except Exception as exc:
        err_msg = str(exc)
        logger.error(f"Gemini API connection test failed: {err_msg}")
        # Specific diagnostic categorization
        if "403" in err_msg or "API_KEY_INVALID" in err_msg or "unregistered callers" in err_msg.lower():
            reason = "Invalid Gemini API Key or caller is not authorized."
        elif "404" in err_msg or "NOT_FOUND" in err_msg or "models/" in err_msg:
            reason = f"Model '{target_model}' not found or not supported with your API key tier."
        elif "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            reason = "Gemini API rate limit or quota exceeded."
        else:
            reason = f"Gemini API connection error: {err_msg}"

        return False, reason, {"error_type": "ApiError", "details": err_msg}


def mask_secret(secret: str) -> str:
    """Mask a secret string for safe display in UI or logs."""
    if not secret:
        return "Not Set"
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}...{secret[-4:]}"
