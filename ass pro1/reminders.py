"""
reminders.py - APScheduler & SMTP notification system for Assignment Deadline Agent.
Manages automated 24h, 3h, and overdue reminders, prevents duplicate notifications,
tracks delivery status in SQLite, provides a test email feature, and ensures single-instance execution.
"""

from __future__ import annotations

import argparse
import logging
import os
import smtplib
import sys
import threading
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional, Tuple

from apscheduler.schedulers.background import BackgroundScheduler

import database
import priority

logger = logging.getLogger(__name__)

# Thread-safe singleton for the background scheduler
_scheduler_lock = threading.Lock()
_global_scheduler: Optional[BackgroundScheduler] = None


def get_smtp_config() -> Dict[str, Any]:
    """Retrieve SMTP configuration from environment variables."""
    return {
        "server": os.getenv("SMTP_SERVER", "smtp.gmail.com").strip(),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "email": os.getenv("SMTP_EMAIL", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", "").strip(),
        "recipient": os.getenv("NOTIFICATION_RECIPIENT_EMAIL", "").strip(),
    }


def send_email(
    recipient: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Send an email via SMTP.
    Returns (success: bool, message_or_error: str).
    Never reports success if SMTP connection, authentication, or sending fails.
    """
    cfg = get_smtp_config()
    server_host = cfg["server"]
    server_port = cfg["port"]
    sender_email = cfg["email"]
    sender_password = cfg["password"]

    if not sender_email or not sender_password:
        return False, "SMTP configuration missing: SMTP_EMAIL or SMTP_PASSWORD is not set."

    if not recipient or "@" not in recipient:
        return False, f"Invalid recipient email: '{recipient}'."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Assignment Deadline Agent <{sender_email}>"
    msg["To"] = recipient

    plain_body = text_content or html_content
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    try:
        if server_port == 465:
            with smtplib.SMTP_SSL(server_host, server_port, timeout=15) as server:
                server.login(sender_email, sender_password)
                server.sendmail(sender_email, [recipient], msg.as_string())
        else:
            with smtplib.SMTP(server_host, server_port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(sender_email, sender_password)
                server.sendmail(sender_email, [recipient], msg.as_string())

        return True, f"Email successfully delivered to {recipient}."
    except smtplib.SMTPAuthenticationError as e:
        return False, f"SMTP Authentication failed: {str(e)}"
    except smtplib.SMTPConnectError as e:
        return False, f"SMTP Connection failed to {server_host}:{server_port}: {str(e)}"
    except Exception as exc:
        return False, f"SMTP Error: {str(exc)}"


def send_test_email(
    recipient: Optional[str] = None,
    target_email: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Tuple[bool, str]:
    """Send a diagnostic test email to verify SMTP configuration and optionally log in SQLite."""
    cfg = get_smtp_config()
    final_recipient = (target_email or recipient or cfg["recipient"]).strip()

    if not final_recipient:
        return False, "No recipient email address specified. Configure NOTIFICATION_RECIPIENT_EMAIL or enter one in the UI."

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = f"Test Notification: Assignment Deadline Agent ({now_str})"
    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: #2563eb; color: white; padding: 16px 20px; border-radius: 8px 8px 0 0;">
          <h2 style="margin: 0;">Assignment Deadline Agent</h2>
          <p style="margin: 4px 0 0; opacity: 0.9;">SMTP Notification Diagnostics</p>
        </div>
        <div style="border: 1px solid #e2e8f0; border-top: none; padding: 20px; border-radius: 0 0 8px 8px; background: #fff;">
          <p>Hello,</p>
          <p>This is a test notification confirming that your SMTP email settings are working correctly.</p>
          <p><strong>Configured SMTP Server:</strong> {cfg['server']}:{cfg['port']}<br>
          <strong>Sender:</strong> {cfg['email']}<br>
          <strong>Timestamp:</strong> {now_str}</p>
          <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
          <p style="font-size: 12px; color: #64748b;">You will receive automated reminders at 24 hours, 3 hours, and when deadlines become overdue.</p>
        </div>
      </body>
    </html>
    """

    success, msg = send_email(final_recipient, subject, html_content)

    if db_path:
        # Find first assignment ID or use 0
        assignments = database.get_assignments(db_path=db_path)
        first_aid = assignments[0]["id"] if assignments else 1
        now_iso = datetime.now(timezone.utc).isoformat()
        database.create_reminder_record(
            assignment_id=first_aid,
            reminder_type="test_email",
            scheduled_time=now_iso,
            delivery_status="SENT" if success else "FAILED",
            recipient=final_recipient,
            sent_at=now_iso if success else None,
            error_message=None if success else msg,
            db_path=db_path,
        )

    return success, msg


def check_and_send_reminders(db_path: str = database.DEFAULT_DB_PATH) -> int:
    """
    Periodic job that checks SQLite for assignments needing 24h, 3h, or overdue reminders.
    - Prevents duplicates (skips if already sent).
    - Strictly skips completed assignments.
    - Stores delivery status in reminder_records.
    Returns the count of successfully sent reminders.
    """
    cfg = get_smtp_config()
    recipient = cfg["recipient"]
    if not recipient:
        logger.debug("No recipient configured for reminders; skipping run.")
        return 0

    all_assignments = database.get_assignments(db_path=db_path)
    now_dt = priority.get_current_time()
    sent_count = 0

    for raw in all_assignments:
        # Strictly skip completed
        if str(raw.get("status", "")).strip().lower() == "completed":
            continue

        aid = raw["id"]
        dl_val = raw.get("deadline") or f"{raw.get('deadline_date')}T{raw.get('deadline_time')}:00"
        try:
            deadline_dt = priority.parse_deadline(str(dl_val))
        except Exception:
            continue

        diff_seconds = (deadline_dt - now_dt).total_seconds()
        diff_hours = diff_seconds / 3600.0

        needed_type: Optional[str] = None

        if diff_seconds < 0:
            # Overdue
            needed_type = "overdue"
        elif 0 <= diff_hours <= 3.0:
            # Within 3 hours
            needed_type = "3h_before"
        elif 3.0 < diff_hours <= 24.0:
            # Within 24 hours
            needed_type = "24h_before"

        if not needed_type:
            continue

        # Prevent duplicate: check if already sent
        if database.has_reminder_been_sent(aid, needed_type, db_path=db_path):
            continue

        # Create record in SCHEDULED status
        rid = database.create_reminder_record(
            assignment_id=aid,
            reminder_type=needed_type,
            scheduled_time=datetime.now(timezone.utc).isoformat(),
            delivery_status="SCHEDULED",
            recipient=recipient,
            db_path=db_path,
        )

        enriched = priority.enrich_assignment(raw, now_dt=now_dt)
        subject_line, html_body = build_reminder_email_content(enriched, needed_type)

        success, msg = send_email(recipient, subject_line, html_body)
        now_iso = datetime.now(timezone.utc).isoformat()

        if success:
            database.update_reminder_status(rid, status="SENT", sent_at=now_iso, db_path=db_path)
            sent_count += 1
            logger.info(f"Sent {needed_type} reminder for assignment #{aid} to {recipient}")
        else:
            database.update_reminder_status(rid, status="FAILED", error_message=msg, db_path=db_path)
            logger.warning(f"Failed to send {needed_type} reminder for #{aid}: {msg}")

    return sent_count


# Compatibility alias
process_scheduled_reminders = check_and_send_reminders


def build_reminder_email_content(
    assignment: Dict[str, Any],
    reminder_type: str,
) -> Tuple[str, str]:
    """Generate subject line and styled HTML template for deadline reminders."""
    title = assignment.get("title", "Untitled Assignment")
    subject_code = assignment.get("subject", "General")
    deadline = assignment.get("deadline") or f"{assignment.get('deadline_date')} {assignment.get('deadline_time')}"
    importance = assignment.get("importance", "Medium")
    effort = assignment.get("estimated_effort", 1.0)
    urgency = assignment.get("urgency", "High")
    remaining = assignment.get("remaining_time", "")

    type_titles = {
        "24h_before": "Due in 24 Hours",
        "3h_before": "URGENT: Due in 3 Hours",
        "overdue": "CRITICAL: Assignment Overdue",
    }
    header_title = type_titles.get(reminder_type, "Assignment Reminder")

    banner_color = "#dc2626" if reminder_type in ["3h_before", "overdue"] else "#ea580c"

    subject_line = f"[{header_title}] {subject_code}: {title}"

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #1e293b; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: {banner_color}; color: white; padding: 18px 24px; border-radius: 8px 8px 0 0;">
          <span style="font-size: 12px; font-weight: bold; text-transform: uppercase; letter-spacing: 1px;">Deadline Notification</span>
          <h2 style="margin: 6px 0 0; font-size: 22px;">{header_title}</h2>
        </div>
        <div style="border: 1px solid #e2e8f0; border-top: none; padding: 24px; border-radius: 0 0 8px 8px; background: #ffffff;">
          <h3 style="margin-top: 0; color: #0f172a;">{title} ({subject_code})</h3>
          <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
            <tr style="border-bottom: 1px solid #f1f5f9;">
              <td style="padding: 8px 0; color: #64748b; font-weight: 500;">Deadline:</td>
              <td style="padding: 8px 0; font-weight: 600; color: #0f172a;">{deadline}</td>
            </tr>
            <tr style="border-bottom: 1px solid #f1f5f9;">
              <td style="padding: 8px 0; color: #64748b; font-weight: 500;">Status / Remaining:</td>
              <td style="padding: 8px 0; font-weight: 600; color: {banner_color};">{remaining}</td>
            </tr>
            <tr style="border-bottom: 1px solid #f1f5f9;">
              <td style="padding: 8px 0; color: #64748b; font-weight: 500;">Importance:</td>
              <td style="padding: 8px 0; font-weight: 600; color: #0f172a;">{importance}</td>
            </tr>
            <tr>
              <td style="padding: 8px 0; color: #64748b; font-weight: 500;">Estimated Effort:</td>
              <td style="padding: 8px 0; font-weight: 600; color: #0f172a;">{effort} hours</td>
            </tr>
          </table>
          <p style="margin: 20px 0 0; font-size: 13px; color: #64748b;">
            Open your Assignment Deadline Agent dashboard to update progress or mark this task as completed.
          </p>
        </div>
      </body>
    </html>
    """
    return subject_line, html


def check_and_send_reminders(db_path: str = database.DEFAULT_DB_PATH) -> int:
    """
    Periodic job that checks SQLite for assignments needing 24h, 3h, or overdue reminders.
    - Prevents duplicates (skips if already sent).
    - Strictly skips completed assignments.
    - Stores delivery status in reminder_records.
    Returns the count of successfully sent reminders.
    """
    cfg = get_smtp_config()
    recipient = cfg["recipient"]
    if not recipient:
        logger.debug("No recipient configured for reminders; skipping run.")
        return 0

    all_assignments = database.get_assignments(db_path=db_path)
    now_dt = priority.get_current_time()
    sent_count = 0

    for raw in all_assignments:
        # Strictly skip completed
        if str(raw.get("status", "")).strip().lower() == "completed":
            continue

        aid = raw["id"]
        dl_val = raw.get("deadline") or f"{raw.get('deadline_date')}T{raw.get('deadline_time')}:00"
        try:
            deadline_dt = priority.parse_deadline(str(dl_val))
        except Exception:
            continue

        diff_seconds = (deadline_dt - now_dt).total_seconds()
        diff_hours = diff_seconds / 3600.0

        needed_type: Optional[str] = None

        if diff_seconds < 0:
            # Overdue
            needed_type = "overdue"
        elif 0 <= diff_hours <= 3.0:
            # Within 3 hours
            needed_type = "3h_before"
        elif 3.0 < diff_hours <= 24.0:
            # Within 24 hours
            needed_type = "24h_before"

        if not needed_type:
            continue

        # Prevent duplicate: check if already sent
        if database.has_reminder_been_sent(aid, needed_type, db_path=db_path):
            continue

        # Create record in SCHEDULED status
        rid = database.create_reminder_record(
            assignment_id=aid,
            reminder_type=needed_type,
            scheduled_time=datetime.now(timezone.utc).isoformat(),
            delivery_status="SCHEDULED",
            recipient=recipient,
            db_path=db_path,
        )

        enriched = priority.enrich_assignment(raw, now_dt=now_dt)
        subject_line, html_body = build_reminder_email_content(enriched, needed_type)

        success, msg = send_email(recipient, subject_line, html_body)
        now_iso = datetime.now(timezone.utc).isoformat()

        if success:
            database.update_reminder_status(rid, status="SENT", sent_at=now_iso, db_path=db_path)
            sent_count += 1
            logger.info(f"Sent {needed_type} reminder for assignment #{aid} to {recipient}")
        else:
            database.update_reminder_status(rid, status="FAILED", error_message=msg, db_path=db_path)
            logger.warning(f"Failed to send {needed_type} reminder for #{aid}: {msg}")

    return sent_count


def start_scheduler(db_path: str = database.DEFAULT_DB_PATH, interval_minutes: int = 2) -> BackgroundScheduler:
    """
    Start the APScheduler background scheduler singleton.
    Thread-safe and safe against Streamlit reruns.
    """
    global _global_scheduler

    with _scheduler_lock:
        if _global_scheduler is not None and _global_scheduler.running:
            return _global_scheduler

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            func=check_and_send_reminders,
            trigger="interval",
            minutes=interval_minutes,
            kwargs={"db_path": db_path},
            id="assignment_deadline_checker",
            replace_existing=True,
        )
        scheduler.start()
        _global_scheduler = scheduler
        logger.info(f"Background reminder scheduler started with {interval_minutes}m interval.")
        return scheduler


def stop_scheduler() -> None:
    """Safely shut down the background scheduler."""
    global _global_scheduler
    with _scheduler_lock:
        if _global_scheduler is not None and _global_scheduler.running:
            _global_scheduler.shutdown(wait=False)
            _global_scheduler = None
            logger.info("Background reminder scheduler stopped.")


def is_scheduler_running() -> bool:
    """Return whether the background scheduler is currently active."""
    global _global_scheduler
    return _global_scheduler is not None and _global_scheduler.running


def run_scheduler_daemon(interval_minutes: int = 2) -> None:
    """Standalone CLI entry point for running the scheduler independently of the Streamlit UI."""
    print("=" * 60)
    print("ASSIGNMENT DEADLINE AGENT — BACKGROUND SCHEDULER SERVICE")
    print("=" * 60)
    print(f"Checking for 24h, 3h, and overdue reminders every {interval_minutes} minutes.")
    print("Press Ctrl+C to stop.")

    database.init_db()
    sched = start_scheduler(interval_minutes=interval_minutes)

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("\nStopping scheduler...")
        stop_scheduler()
        print("Scheduler stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Assignment Deadline Agent Reminder Scheduler")
    parser.add_argument("--interval", type=int, default=2, help="Check interval in minutes (default 2)")
    parser.add_argument("--check-now", action="store_true", help="Run a single immediate check and exit")
    parser.add_argument("--test-email", type=str, help="Send a test email to the given recipient")
    args = parser.parse_args()

    if args.test_email:
        s, m = send_test_email(args.test_email)
        print(f"Result: {s} - {m}")
        sys.exit(0 if s else 1)
    elif args.check_now:
        c = check_and_send_reminders()
        print(f"Immediate check completed. Reminders sent: {c}")
        sys.exit(0)
    else:
        run_scheduler_daemon(interval_minutes=args.interval)
