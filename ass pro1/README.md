# ASSIGNMENT DEADLINE AGENT — MULTI-AGENT AI SYSTEM

An autonomous, production-ready Multi-Agent AI system designed for students and educators to track academic assignment deadlines, prioritize urgent coursework, autonomously generate audited daily study plans, and trigger automated multi-stage email reminders.

Built with **Google's official Gen AI SDK (`google-genai`)**, **Gemini API**, **SQLite**, **APScheduler**, and **Streamlit**.

---

## 🌟 Architecture & Multi-Agent Roles

The system employs three separate, cooperating AI agent roles with distinct system prompts and responsibilities:

```
Student Request
      │
      ▼
Planner Agent (Analyzes deadlines, effort, importance)
      │
      ▼
Worker Agent (Executes approved SQLite tools)
      │
      ▼
Planner generates structured daily plan
      │
      ▼
Reviewer Agent (Independently audits against DB facts & deterministic rules)
      │
      ├── Needs Revision ──> Planner retries once (feedback incorporated)
      │
      ▼
Validated Result
      │
      ▼
Streamlit Interactive Dashboard
```

### 1. Planner Agent (`agents/planner.py`)
- Analyzes student assignments and creates a prioritized plan.
- Retrieves assignment data from approved database tools.
- Considers deadline urgency, importance, and estimated effort.
- Recommends which assignment to complete first with practical reasons.
- Produces a structured daily task plan in validated JSON format.
- Strictly adheres to actual assignment records and never invents deadlines or assignments.

### 2. Worker Agent (`agents/worker.py`)
- Executes approved assignment-management actions.
- Uses Gemini function calling with explicitly registered tools:
  - `add_assignment`
  - `get_assignments`
  - `get_assignment_details`
  - `update_assignment`
  - `mark_assignment_completed`
  - `get_upcoming_deadlines`
  - `generate_daily_plan`
  - `create_reminder_schedule`
  - `delete_assignment` (safety guardrail: requires user confirmation)
- All database changes are performed by Python functions, never fabricated by the LLM.
- Validates every tool argument and logs complete execution traces.

### 3. Reviewer Agent (`agents/reviewer.py`)
- Independently reviews the Planner's proposed daily plan.
- Authoritative Python validation verifies assignment IDs against SQLite.
- Confirms deadline consistency and that completed assignments are strictly excluded.
- Verifies that overdue tasks are marked as `CRITICAL`.
- Verifies priority conforms to deterministic Python rules.
- Identifies missing urgent assignments.
- Returns structured decision: `APPROVED` or `NEEDS_REVISION`.
- If rejected, triggers a controlled one-retry revision loop to prevent excessive API consumption.

---

## 🚀 Exact Windows Setup Terminal Commands

Follow these exact commands in Windows PowerShell or Command Prompt:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

*(If PowerShell displays an execution policy error when activating, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first).*

---

## 🔑 Environment Configuration

Create a `.env` file in the project root by copying `.env.example`:

```powershell
copy .env.example .env
```

### 1. Obtain a Gemini API Key from Google AI Studio
1. Navigate to [Google AI Studio](https://aistudio.google.com/).
2. Sign in with your Google account.
3. Click **"Get API key"** and create or select an API key.
4. Copy the key and add it to `.env`:

```env
GEMINI_API_KEY=AIzaSyYourActualKeyHere
```

### 2. Configure the Three Model IDs
Each agent role is independently configurable in `.env` without modifying code:

```env
PLANNER_MODEL=gemini-3.8-flash
WORKER_MODEL=gemini-3.8-flash
REVIEWER_MODEL=gemini-3.8-flash
GEMINI_MODEL=gemini-2.5-flash
```

*(You can also use `gemini-2.5-flash` or `gemini-1.5-flash` depending on your Google AI Studio access).*

### 3. Configure SMTP Email Notifications (Optional)
To enable automated email alerts at 24 hours, 3 hours, and when overdue:

```env
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_EMAIL=your_email@gmail.com
SMTP_PASSWORD=your_gmail_app_password
NOTIFICATION_RECIPIENT_EMAIL=student@example.com
```

> **Note for Gmail users:** Generate an **App Password** via Google Account -> Security -> 2-Step Verification -> App Passwords. Never use your main account password.

---

## 🧪 Testing

### 1. Run Automated Unit Tests (Fast & Mocked)
All external Gemini API and SMTP calls are mocked in automated tests so they do not consume API credits or send real emails:

```powershell
.venv\Scripts\pytest -v
```

Tests verify:
- Assignment creation, retrieval, updating, completion, and deletion.
- Database persistence and parameterized queries.
- Deterministic priority calculation, overdue detection, and time remaining.
- Completed assignment filtering.
- Tool argument validation.
- Planner structured JSON output validation.
- Reviewer rejection of incorrect/hallucinated plans and approval of valid plans.
- One-retry revision workflow in Orchestrator.
- Duplicate reminder prevention.
- Email failure handling and delivery logging.
- Missing and invalid Gemini API key handling.

### 2. Run Optional Live Gemini API Connection Test
To verify live connectivity against Google Gemini API with your real key:

```powershell
python test_live_api.py
```

*(You can also test live API connectivity directly from the **Settings** tab in the Streamlit UI).*

---

## ⏰ Background Reminder Scheduler

APScheduler checks for upcoming deadlines and dispatches email alerts:
- **24-hour reminder**
- **3-hour reminder**
- **Overdue notification**

The scheduler is started automatically within the Streamlit process (using safe singleton caching).

### Running Independent Background Scheduler Service
If you want reminders to continue running even when the Streamlit UI browser tab is closed, run the standalone background daemon:

```powershell
python reminders.py
```

Options:
```powershell
python reminders.py --interval 2          # Check every 2 minutes
python reminders.py --check-now            # Run a single immediate check
python reminders.py --test-email student@example.com   # Send a test email
```

---

## 🖥️ Streamlit Dashboard Sections

1. **📊 Dashboard**: Metrics (Total, Pending, Completed, Overdue, Due in 24h), completion progress bar, today's prioritized tasks, upcoming 72h deadlines.
2. **➕ Add Assignment**: Validated assignment creation with real-time priority preview and optional automated reminder scheduling.
3. **📋 All Assignments**: Full search, status filters, subject filters, sorting, inline completion, editing, and deletion with confirmation.
4. **🤖 AI Daily Planner**: Multi-agent orchestration with live agent audit cards (Planner, Worker, Reviewer), revision loop indicator, priority badges, and rationale.
5. **💬 AI Chat Assistant**: Natural-language conversational interface with quick prompts and transparent expandable database tool execution traces.
6. **⏰ Reminders**: Upcoming reminder schedule, delivery status logs (`SENT`, `SCHEDULED`, `FAILED`), SMTP configuration status, and test email sender.
7. **⚙️ Settings**: Live Gemini API connection test, model ID configuration display, timezone setting, safe setup instructions (zero exposed credentials).

---

## 🔒 Security & Best Practices
- **No Hardcoded Secrets**: All credentials loaded strictly from `.env`.
- **Zero Secret Exposure**: Passwords and keys are never rendered in the UI or printed to terminal logs.
- **SQL Injection Prevention**: All queries use parameterized SQL in `sqlite3`.
- **Destructive Actions Guardrail**: Assignment deletion permanently requires explicit user confirmation.
- **Deterministic Python Authority**: Dates, overdue statuses, and priority calculations are authoritative in Python and cannot be hallucinated or overridden by the LLM.
