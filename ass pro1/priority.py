"""
priority.py - Deterministic Priority Engine for Assignment Deadline Agent.
Calculates timezone-aware urgency, time remaining, and multi-factor priority scores.
Independent of LLM - authoritative deterministic Python rules.

Priority Rules:
- Overdue and pending: CRITICAL
- Due within 24 hours: HIGH
- Due within 72 hours: MEDIUM
- More than 72 hours away: NORMAL
- Completed assignments: EXCLUDED FROM PENDING TASKS
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore


DEFAULT_TIMEZONE_NAME = os.getenv("APP_TIMEZONE", "UTC")


def get_configured_timezone(tz_name: Optional[str] = None) -> ZoneInfo:
    """Return a ZoneInfo object, falling back to UTC if invalid."""
    target_tz = tz_name or os.getenv("APP_TIMEZONE", DEFAULT_TIMEZONE_NAME)
    try:
        return ZoneInfo(target_tz)
    except Exception:
        return ZoneInfo("UTC")


def get_current_time(tz_name: Optional[str] = None) -> datetime:
    """Return the current timezone-aware datetime."""
    tz = get_configured_timezone(tz_name)
    return datetime.now(tz)


def parse_deadline(
    deadline_input: str,
    time_str: Optional[str] = None,
    tz_name: Optional[str] = None,
) -> datetime:
    """
    Parse a deadline into a timezone-aware datetime.
    Supports:
    - Single ISO string: '2026-10-15T18:00:00', '2026-10-15 18:00'
    - Date string + time string: '2026-10-15', '18:00'
    """
    tz = get_configured_timezone(tz_name)
    cleaned = str(deadline_input).strip()

    if time_str is not None:
        # Two-argument form: date and time
        cleaned_time = str(time_str).strip()
        parts = cleaned_time.split(":")
        if len(parts) == 2:
            cleaned_time = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:00"
        elif len(parts) == 3:
            cleaned_time = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
        dt_str = f"{cleaned} {cleaned_time}"
        naive_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        return naive_dt.replace(tzinfo=tz)

    # Single-string form
    if "T" in cleaned:
        cleaned = cleaned.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(cleaned)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=tz)
            return parsed.astimezone(tz)
        except Exception:
            pass

    # Try common formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive_dt = datetime.strptime(cleaned, fmt)
            if fmt == "%Y-%m-%d":
                # Default end of day if only date is provided
                naive_dt = naive_dt.replace(hour=23, minute=59, second=59)
            return naive_dt.replace(tzinfo=tz)
        except ValueError:
            continue

    # Fallback to current time if parsing completely fails
    return get_current_time(tz_name)


class UrgencyStr(str):
    """String subclass that allows case-insensitive comparison and matches aliases."""
    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            s_self = str(self).strip().lower()
            s_other = other.strip().lower()
            if s_self == s_other:
                return True
            if s_self in ("completed", "excluded from pending tasks") and s_other in ("completed", "excluded from pending tasks"):
                return True
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.lower())


def calculate_urgency(
    deadline_dt: datetime,
    now_dt: Optional[datetime] = None,
    is_completed: bool = False,
) -> UrgencyStr:
    """
    Deterministic calculation of deadline urgency:
    - If completed: 'Completed' / 'EXCLUDED FROM PENDING TASKS'
    - Overdue and pending: 'Critical' / 'CRITICAL'
    - Due within 24 hours: 'High' / 'HIGH'
    - Due within 72 hours: 'Medium' / 'MEDIUM'
    - More than 72 hours away: 'Normal' / 'NORMAL'
    """
    if is_completed:
        return UrgencyStr("Completed")

    if now_dt is None:
        tz = deadline_dt.tzinfo or timezone.utc
        now_dt = datetime.now(tz)
    elif now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=deadline_dt.tzinfo or timezone.utc)

    # Ensure same timezone for comparison
    if deadline_dt.tzinfo is not None and now_dt.tzinfo is not None:
        now_dt_cmp = now_dt.astimezone(deadline_dt.tzinfo)
    else:
        now_dt_cmp = now_dt

    diff_seconds = (deadline_dt - now_dt_cmp).total_seconds()

    if diff_seconds < 0:
        return UrgencyStr("Critical")
    elif diff_seconds <= 24 * 3600:
        return UrgencyStr("High")
    elif diff_seconds <= 72 * 3600:
        return UrgencyStr("Medium")
    else:
        return UrgencyStr("Normal")


def format_remaining_time(
    deadline_dt: datetime,
    now_dt: Optional[datetime] = None,
) -> Tuple[str, bool]:
    """
    Format remaining time into human-friendly string and indicate whether overdue.
    Returns (formatted_string, is_overdue).
    """
    if now_dt is None:
        tz = deadline_dt.tzinfo or timezone.utc
        now_dt = datetime.now(tz)
    elif now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=deadline_dt.tzinfo or timezone.utc)

    if deadline_dt.tzinfo is not None and now_dt.tzinfo is not None:
        now_dt_cmp = now_dt.astimezone(deadline_dt.tzinfo)
    else:
        now_dt_cmp = now_dt

    diff_seconds = (deadline_dt - now_dt_cmp).total_seconds()
    is_overdue = diff_seconds < 0
    abs_seconds = abs(diff_seconds)

    days = int(abs_seconds // 86400)
    hours = int((abs_seconds % 86400) // 3600)
    minutes = int((abs_seconds % 3600) // 60)

    if days > 0:
        time_part = f"{days}d {hours}h"
    elif hours > 0:
        time_part = f"{hours}h {minutes}m"
    else:
        time_part = f"{minutes}m"

    if is_overdue:
        return f"Overdue by {time_part}", True
    else:
        return f"Due in {time_part}", False


def calculate_priority_score(
    assignment: Dict[str, Any],
    now_dt: Optional[datetime] = None,
) -> float:
    """
    Deterministic scoring considering urgency, importance, and estimated effort.
    Higher score indicates higher priority.
    """
    status_str = str(assignment.get("status", "")).strip().lower()
    is_completed = status_str == "completed"
    if is_completed:
        return -1000.0

    dl_val = assignment.get("deadline")
    dl_date = assignment.get("deadline_date")
    dl_time = assignment.get("deadline_time")

    if dl_val:
        deadline_dt = parse_deadline(str(dl_val))
    elif dl_date and dl_time:
        deadline_dt = parse_deadline(str(dl_date), str(dl_time))
    elif dl_date:
        deadline_dt = parse_deadline(str(dl_date))
    else:
        deadline_dt = get_current_time()

    urgency = calculate_urgency(deadline_dt, now_dt=now_dt, is_completed=is_completed)

    urgency_weights = {
        "CRITICAL": 100.0,
        "HIGH": 70.0,
        "MEDIUM": 40.0,
        "NORMAL": 10.0,
    }
    urgency_score = urgency_weights.get(str(urgency).strip().upper(), 10.0)

    importance_weights = {
        "URGENT": 40.0,
        "HIGH": 30.0,
        "MEDIUM": 20.0,
        "LOW": 10.0,
    }
    importance_str = str(assignment.get("importance", "Medium")).strip()
    importance_score = importance_weights.get(importance_str.upper(), 20.0)

    # Effort consideration
    try:
        effort = float(assignment.get("estimated_effort", 1.0))
        if effort <= 0:
            effort = 1.0
    except (ValueError, TypeError):
        effort = 1.0

    if urgency.upper() == "CRITICAL":
        effort_factor = effort * 2.0
    elif urgency.upper() == "HIGH":
        effort_factor = effort * 1.0
    else:
        effort_factor = max(0.0, (5.0 - effort) * 0.5)

    return urgency_score + importance_score + effort_factor


def enrich_assignment(
    assignment: Dict[str, Any],
    now_dt: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Enrich an assignment dictionary with urgency, remaining time, overdue flag, and priority score."""
    enriched = dict(assignment)
    status_str = str(enriched.get("status", "")).strip().lower()
    is_completed = status_str == "completed"

    dl_val = enriched.get("deadline")
    dl_date = enriched.get("deadline_date")
    dl_time = enriched.get("deadline_time")

    try:
        if dl_val:
            deadline_dt = parse_deadline(str(dl_val))
        elif dl_date and dl_time:
            deadline_dt = parse_deadline(str(dl_date), str(dl_time))
        elif dl_date:
            deadline_dt = parse_deadline(str(dl_date))
        else:
            deadline_dt = get_current_time()

        urgency = calculate_urgency(deadline_dt, now_dt=now_dt, is_completed=is_completed)
        remaining_str, is_overdue = format_remaining_time(deadline_dt, now_dt=now_dt)
    except Exception:
        urgency = "NORMAL"
        remaining_str = "Invalid Date"
        is_overdue = False

    score = calculate_priority_score(enriched, now_dt=now_dt)

    enriched["urgency"] = urgency
    enriched["remaining_time"] = remaining_str
    enriched["is_overdue"] = is_overdue and not is_completed
    enriched["priority_score"] = round(score, 2)
    return enriched


def sort_assignments_by_priority(
    assignments: List[Dict[str, Any]],
    now_dt: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Enrich and sort assignments by priority score descending."""
    enriched_list = [enrich_assignment(a, now_dt=now_dt) for a in assignments]
    return sorted(enriched_list, key=lambda x: x["priority_score"], reverse=True)


def get_recommended_next_task(
    assignments: List[Dict[str, Any]],
    now_dt: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Return the highest priority pending assignment along with a deterministic rationale."""
    pending = [a for a in assignments if str(a.get("status", "")).strip().lower() != "completed"]
    if not pending:
        return None

    sorted_pending = sort_assignments_by_priority(pending, now_dt=now_dt)
    top_task = sorted_pending[0]

    urgency = top_task["urgency"]
    importance = top_task.get("importance", "Medium")
    effort = top_task.get("estimated_effort", 1.0)
    rem = top_task.get("remaining_time", "")

    rationale_parts = []
    if urgency.upper() == "CRITICAL":
        rationale_parts.append(f"It is OVERDUE ({rem}) and requires immediate resolution.")
    elif urgency.upper() == "HIGH":
        rationale_parts.append(f"It is due within 24 hours ({rem}) with high urgency.")
    elif urgency.upper() == "MEDIUM":
        rationale_parts.append(f"It is due within 72 hours ({rem}).")
    else:
        rationale_parts.append(f"Upcoming deadline ({rem}).")

    rationale_parts.append(f"Importance rating: {importance}.")
    rationale_parts.append(f"Estimated effort: {effort} hours.")

    top_task["recommendation_reason"] = " ".join(rationale_parts)
    return top_task
