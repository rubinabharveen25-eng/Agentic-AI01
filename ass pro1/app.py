"""
app.py - Streamlit Frontend for ASSIGNMENT DEADLINE AGENT — MULTI-AGENT AI SYSTEM.
Features three distinct Gemini agent roles: Planner, Worker, and Reviewer.
Provides persistent SQLite assignment management, deterministic priority calculations,
multi-agent daily planning with audit loop, AI chat assistant, and APScheduler/SMTP reminders.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

import streamlit as st
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

import config
import database
import priority
import reminders
import tools
from agents.orchestrator import Orchestrator

# --- Streamlit Page Configuration ---
st.set_page_config(
    page_title="Assignment Deadline Agent — Multi-Agent AI",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize SQLite database
database.init_db()

# Safe singleton scheduler initialization for Streamlit
@st.cache_resource
def get_background_scheduler():
    try:
        return reminders.start_scheduler(interval_minutes=2)
    except Exception:
        return None

scheduler_instance = get_background_scheduler()

# --- Custom Styling & SaaS UI Theme ---
st.markdown(
    """
    <style>
    /* Global polish */
    .main { background-color: #f8fafc; }
    h1, h2, h3 { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-weight: 700; color: #0f172a; }
    
    /* SaaS Metric Cards */
    .metric-card {
        background: #ffffff;
        border-radius: 12px;
        padding: 20px 24px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        transition: transform 0.15s ease-in-out, box-shadow 0.15s ease-in-out;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.08);
    }
    .metric-title {
        font-size: 13px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #64748b;
        margin-bottom: 8px;
    }
    .metric-value {
        font-size: 32px;
        font-weight: 800;
        color: #0f172a;
        line-height: 1;
    }
    .metric-sub {
        font-size: 12px;
        color: #94a3b8;
        margin-top: 6px;
    }

    /* Consistent Priority Badges */
    .badge-critical {
        background-color: #fee2e2;
        color: #991b1b;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 11px;
        border: 1px solid #f87171;
        display: inline-block;
    }
    .badge-high {
        background-color: #ffedd5;
        color: #9a3412;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 11px;
        border: 1px solid #fb923c;
        display: inline-block;
    }
    .badge-medium {
        background-color: #dbeafe;
        color: #1e40af;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 11px;
        border: 1px solid #93c5fd;
        display: inline-block;
    }
    .badge-normal {
        background-color: #f1f5f9;
        color: #475569;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 11px;
        border: 1px solid #cbd5e1;
        display: inline-block;
    }
    .badge-completed {
        background-color: #dcfce7;
        color: #166534;
        padding: 4px 10px;
        border-radius: 9999px;
        font-weight: 700;
        font-size: 11px;
        border: 1px solid #86efac;
        display: inline-block;
    }

    /* Agent status cards */
    .agent-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 12px;
    }
    .agent-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;
    }
    .agent-name {
        font-weight: 700;
        font-size: 15px;
        color: #1e293b;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_priority_badge_html(urgency: str) -> str:
    """Return colored HTML badge for a priority/urgency string."""
    u_clean = str(urgency).strip().upper()
    if u_clean == "CRITICAL":
        return f'<span class="badge-critical">CRITICAL</span>'
    elif u_clean == "HIGH":
        return f'<span class="badge-high">HIGH</span>'
    elif u_clean == "MEDIUM":
        return f'<span class="badge-medium">MEDIUM</span>'
    elif u_clean in ["COMPLETED", "EXCLUDED FROM PENDING TASKS"]:
        return f'<span class="badge-completed">COMPLETED</span>'
    else:
        return f'<span class="badge-normal">NORMAL</span>'


# --- Sidebar Navigation ---
st.sidebar.image("https://img.icons8.com/fluency/96/books.png", width=64)
st.sidebar.title("Assignment Agent")
st.sidebar.caption("Multi-Agent AI Productivity System")

# Active Agent Roles Indicator in Sidebar
with st.sidebar.expander("🤖 Agent Roles Active", expanded=False):
    st.markdown(
        f"""
        - **Planner:** `{config.get_planner_model()}`
        - **Worker:** `{config.get_worker_model()}`
        - **Reviewer:** `{config.get_reviewer_model()}`
        """
    )
    api_key_configured = bool(config.get_gemini_api_key() and config.get_gemini_api_key() != "your_actual_key_here")
    if api_key_configured:
        st.success("API Key: Configured")
    else:
        st.warning("API Key: Not Configured")

menu_choice = st.sidebar.radio(
    "Navigation",
    [
        "📊 Dashboard",
        "➕ Add Assignment",
        "📋 All Assignments",
        "🤖 AI Daily Planner",
        "💬 AI Chat Assistant",
        "⏰ Reminders",
        "⚙️ Settings",
    ],
    index=0,
)

st.sidebar.divider()
st.sidebar.caption("Built with Google Gen AI SDK & Streamlit")


# ==============================================================================
# SECTION A: DASHBOARD
# ==============================================================================
if menu_choice == "📊 Dashboard":
    st.title("📊 Student Academic Dashboard")
    st.write("Real-time assignment tracking with deterministic deadline prioritization.")

    # Fetch fresh assignments from SQLite
    all_assignments = database.get_assignments()
    enriched_all = [priority.enrich_assignment(a) for a in all_assignments]
    pending = [a for a in enriched_all if str(a.get("status", "")).strip().lower() != "completed"]
    completed = [a for a in enriched_all if str(a.get("status", "")).strip().lower() == "completed"]
    overdue = [a for a in pending if a.get("is_overdue") or a.get("urgency", "").upper() == "CRITICAL"]
    due_24h = [a for a in pending if a.get("urgency", "").upper() == "HIGH"]

    # Top Metric Cards
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Total Tasks</div>
                <div class="metric-value">{len(all_assignments)}</div>
                <div class="metric-sub">Across all subjects</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Pending</div>
                <div class="metric-value">{len(pending)}</div>
                <div class="metric-sub">Need completion</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Completed</div>
                <div class="metric-value" style="color: #16a34a;">{len(completed)}</div>
                <div class="metric-sub">Successfully done</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Overdue</div>
                <div class="metric-value" style="color: #dc2626;">{len(overdue)}</div>
                <div class="metric-sub">Critical attention</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c5:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Due in 24h</div>
                <div class="metric-value" style="color: #ea580c;">{len(due_24h)}</div>
                <div class="metric-sub">High urgency</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")

    # Progress & Completion Metrics
    if all_assignments:
        pct = len(completed) / len(all_assignments)
        st.progress(pct, text=f"Overall Completion Progress: {int(pct * 100)}% ({len(completed)} of {len(all_assignments)} tasks completed)")
    else:
        st.info("No assignments recorded yet. Use the 'Add Assignment' tab to get started!")

    # Overdue Critical Alert Banner
    if overdue:
        st.error(
            f"⚠️ **ACTION REQUIRED:** You have **{len(overdue)} overdue assignment(s)**! "
            f"Overdue tasks are flagged as CRITICAL priority."
        )

    st.divider()

    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.subheader("🎯 Prioritized Today's Tasks")
        if not pending:
            st.success("🎉 You're completely caught up! No pending assignments.")
        else:
            sorted_pending = priority.sort_assignments_by_priority(pending)
            for idx, task in enumerate(sorted_pending[:5]):
                with st.container():
                    t_col1, t_col2, t_col3 = st.columns([4, 2, 2])
                    with t_col1:
                        st.markdown(f"**{task['title']}** (`{task['subject']}`)")
                        st.caption(f"Effort: {task.get('estimated_effort', 1.0)} hrs | Importance: {task.get('importance', 'Medium')}")
                    with t_col2:
                        st.markdown(get_priority_badge_html(task.get("urgency", "NORMAL")), unsafe_allow_html=True)
                        st.caption(task.get("remaining_time", ""))
                    with t_col3:
                        if st.button("Mark Done", key=f"dash_done_{task['id']}"):
                            database.mark_assignment_completed(task["id"])
                            st.success(f"Completed '{task['title']}'!")
                            st.rerun()
                    st.divider()

    with col_right:
        st.subheader("⏳ Upcoming Deadlines (Next 72h)")
        upcoming_res = tools.tool_get_upcoming_deadlines(hours_ahead=72)
        upcoming_list = upcoming_res.get("upcoming_assignments", [])
        if not upcoming_list:
            st.info("No deadlines approaching within the next 72 hours.")
        else:
            for item in upcoming_list:
                st.markdown(
                    f"""
                    <div style="background: white; border-radius: 8px; border: 1px solid #e2e8f0; padding: 12px; margin-bottom: 8px;">
                        <div style="font-weight: 600; color: #1e293b;">{item['title']} <span style="font-weight: 400; color: #64748b;">({item['subject']})</span></div>
                        <div style="font-size: 13px; color: #ea580c; font-weight: 600; margin-top: 4px;">{item.get('remaining_time')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# ==============================================================================
# SECTION B: ADD ASSIGNMENT
# ==============================================================================
elif menu_choice == "➕ Add Assignment":
    st.title("➕ Add New Assignment")
    st.write("Add student coursework with validated deadlines, importance, and effort.")

    with st.form("add_assignment_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            title_input = st.text_input("Assignment Title *", placeholder="e.g. Operating Systems Lab 3")
            subject_input = st.text_input("Subject / Course Code *", placeholder="e.g. CS301, MATH200")
            importance_input = st.selectbox("Importance Level", ["Urgent", "High", "Medium", "Low"], index=2)

        with col2:
            default_date = date.today() + timedelta(days=2)
            deadline_date_input = st.date_input("Deadline Date *", value=default_date)
            deadline_time_input = st.time_input("Deadline Time (24h) *", value=time(23, 59))
            effort_input = st.number_input("Estimated Effort (Hours) *", min_value=0.25, max_value=80.0, value=2.0, step=0.5)

        description_input = st.text_area("Description / Notes", placeholder="Instructions, requirements, or syllabus links...")

        auto_remind = st.checkbox("Automatically schedule 24-hour and 3-hour email reminders", value=True)

        submitted = st.form_submit_button("💾 Save Assignment", use_container_width=True)

        if submitted:
            d_date_str = deadline_date_input.strftime("%Y-%m-%d")
            d_time_str = deadline_time_input.strftime("%H:%M")

            result = tools.tool_add_assignment(
                title=title_input,
                subject=subject_input,
                deadline_date=d_date_str,
                deadline_time=d_time_str,
                description=description_input,
                importance=importance_input,
                estimated_effort=effort_input,
            )

            if result.get("success"):
                new_id = result.get("assignment_id")
                st.success(f"✅ Successfully added '{title_input}' (ID: {new_id}) to SQLite database!")
                if auto_remind and new_id:
                    rem_res = tools.tool_create_reminder_schedule(assignment_id=new_id)
                    if rem_res.get("success"):
                        st.info(f"⏰ Scheduled {rem_res.get('scheduled_count')} automated reminders.")
                st.balloons()
            else:
                st.error(f"❌ Error: {result.get('error')}")


# ==============================================================================
# SECTION C: ALL ASSIGNMENTS
# ==============================================================================
elif menu_choice == "📋 All Assignments":
    st.title("📋 All Assignments")
    st.write("Browse, filter, edit, complete, or delete assignment records.")

    # Search & Filter Controls
    col_f1, col_f2, col_f3, col_f4 = st.columns([3, 2, 2, 2])
    with col_f1:
        search_query = st.text_input("🔍 Search", placeholder="Title, subject, or description...")
    with col_f2:
        status_filter = st.selectbox("Status Filter", ["All", "Pending", "Completed"], index=0)
    with col_f3:
        all_raw = database.get_assignments()
        subjects = sorted(list({a["subject"] for a in all_raw if a.get("subject")}))
        subject_filter = st.selectbox("Subject Filter", ["All"] + subjects, index=0)
    with col_f4:
        sort_by = st.selectbox("Sort By", ["Deadline (Earliest)", "Priority Score (Highest)", "Effort (Descending)"])

    # Query filtered assignments
    filtered = database.get_assignments(
        status=status_filter if status_filter != "All" else None,
        subject=subject_filter if subject_filter != "All" else None,
        search=search_query if search_query else None,
    )
    enriched_filtered = [priority.enrich_assignment(a) for a in filtered]

    # Sort
    if sort_by == "Deadline (Earliest)":
        enriched_filtered.sort(key=lambda x: str(x.get("deadline", "")))
    elif sort_by == "Priority Score (Highest)":
        enriched_filtered.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
    elif sort_by == "Effort (Descending)":
        enriched_filtered.sort(key=lambda x: float(x.get("estimated_effort", 0)), reverse=True)

    st.write(f"Showing **{len(enriched_filtered)}** assignment(s):")

    if not enriched_filtered:
        st.info("No assignments match your search or filter criteria.")
    else:
        for a in enriched_filtered:
            aid = a["id"]
            is_done = str(a.get("status", "")).strip().lower() == "completed"

            with st.expander(
                f"{'✅ ' if is_done else '📌 '}{a['title']} — {a['subject']} "
                f"({a.get('remaining_time', 'Due ' + str(a.get('deadline_date')))})"
            ):
                col_det1, col_det2 = st.columns([3, 2])
                with col_det1:
                    st.markdown(f"**Description:** {a.get('description') or '*(None provided)*'}")
                    st.write(f"**Deadline:** `{a.get('deadline_date')} {a.get('deadline_time')}`")
                    st.write(f"**Importance:** {a.get('importance', 'Medium')} | **Effort:** {a.get('estimated_effort', 1.0)} hrs")
                    st.markdown(
                        f"**Urgency:** {get_priority_badge_html(a.get('urgency', 'NORMAL'))}",
                        unsafe_allow_html=True,
                    )

                with col_det2:
                    st.markdown("**Actions:**")
                    b_col1, b_col2 = st.columns(2)
                    with b_col1:
                        if not is_done:
                            if st.button("Mark Completed", key=f"btn_done_{aid}"):
                                database.mark_assignment_completed(aid)
                                st.success(f"Assignment #{aid} marked completed!")
                                st.rerun()
                        else:
                            if st.button("Mark Pending", key=f"btn_pend_{aid}"):
                                database.update_assignment(aid, status="Pending")
                                st.rerun()
                    with b_col2:
                        # Deletion requires explicit user confirmation
                        confirm_delete = st.checkbox("Confirm Delete", key=f"conf_del_{aid}")
                        if st.button("🗑️ Delete", key=f"btn_del_{aid}"):
                            del_res = tools.tool_delete_assignment(aid, confirmed=confirm_delete)
                            if del_res.get("success"):
                                st.success(f"Deleted assignment #{aid}!")
                                st.rerun()
                            else:
                                st.warning(del_res.get("message") or del_res.get("error"))

                # Edit Sub-Form
                with st.expander(f"✏️ Edit Assignment #{aid}"):
                    with st.form(f"edit_form_{aid}"):
                        new_title = st.text_input("Title", value=a["title"])
                        new_subj = st.text_input("Subject", value=a["subject"])
                        new_desc = st.text_area("Description", value=a.get("description", ""))
                        new_effort = st.number_input("Effort (hours)", value=float(a.get("estimated_effort", 1.0)), min_value=0.25)
                        new_imp = st.selectbox(
                            "Importance",
                            ["Urgent", "High", "Medium", "Low"],
                            index=["Urgent", "High", "Medium", "Low"].index(a.get("importance", "Medium"))
                            if a.get("importance") in ["Urgent", "High", "Medium", "Low"] else 2,
                        )
                        saved_edit = st.form_submit_button("Save Changes")
                        if saved_edit:
                            database.update_assignment(
                                assignment_id=aid,
                                title=new_title,
                                subject=new_subj,
                                description=new_desc,
                                importance=new_imp,
                                estimated_effort=new_effort,
                            )
                            st.success("Updated assignment!")
                            st.rerun()


# ==============================================================================
# SECTION D: AI DAILY PLANNER (MULTI-AGENT SYSTEM)
# ==============================================================================
elif menu_choice == "🤖 AI Daily Planner":
    st.title("🤖 Multi-Agent AI Daily Planner")
    st.write(
        "Orchestrates three independent Gemini agents to analyze, prioritize, and independently audit your study plan."
    )

    # Workflow architecture visual
    st.markdown(
        """
        <div style="background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 18px; margin-bottom: 20px; font-size: 13px;">
            <strong>Multi-Agent Architecture:</strong>
            <code>Student Request</code> ➔ 
            <span style="color: #2563eb; font-weight: 600;">Worker Agent</span> (SQLite Data) ➔ 
            <span style="color: #9333ea; font-weight: 600;">Planner Agent</span> (Daily Plan) ➔ 
            <span style="color: #059669; font-weight: 600;">Reviewer Agent</span> (Audit & Verify) ➔ 
            <span style="font-weight: 600;">Dashboard</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    plan_col1, plan_col2 = st.columns([2, 1])
    with plan_col1:
        hours_input = st.slider("Available Study Hours Today", min_value=1.0, max_value=14.0, value=8.0, step=0.5)
    with plan_col2:
        generate_btn = st.button("🚀 Generate Daily Plan", type="primary", use_container_width=True)

    # State container for plan results
    if "latest_plan_result" not in st.session_state:
        st.session_state.latest_plan_result = None

    if generate_btn:
        api_key = config.get_gemini_api_key()
        if not api_key or api_key == "your_actual_key_here":
            st.error("⚠️ GEMINI_API_KEY is not configured! Please set your valid API key in the **Settings** tab.")
        else:
            with st.spinner("Multi-Agent AI Pipeline in progress..."):
                orchestrator = Orchestrator()
                result = orchestrator.run_daily_planner_pipeline(available_hours=hours_input)
                st.session_state.latest_plan_result = result

    # Display Pipeline Stages & Agent Status
    if st.session_state.latest_plan_result:
        res = st.session_state.latest_plan_result

        st.subheader("📡 Agent Execution Audit Trail")
        stage_cols = st.columns(len(res.stages))
        for idx, stage in enumerate(res.stages):
            with stage_cols[idx]:
                status_icon = "✅" if stage.status == "SUCCESS" else ("⚠️" if stage.status == "WARNING" else "❌")
                st.markdown(
                    f"""
                    <div class="agent-card">
                        <div class="agent-header">
                            <span class="agent-name">{stage.agent_role}</span>
                            <span>{status_icon}</span>
                        </div>
                        <div style="font-size: 12px; font-weight: 600; color: #64748b;">{stage.stage_name}</div>
                        <div style="font-size: 12px; color: #1e293b; margin-top: 6px;">{stage.message}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # Reviewer Final Verdict Badge
        dec_color = "#16a34a" if res.reviewer_decision == "APPROVED" else "#ea580c"
        st.markdown(
            f"""
            <div style="background: white; border-radius: 8px; border-left: 5px solid {dec_color}; padding: 14px 20px; margin: 16px 0; border-top: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0;">
                <span style="font-size: 12px; text-transform: uppercase; color: #64748b; font-weight: bold;">Reviewer Final Verdict</span>
                <h3 style="margin: 4px 0 0; color: {dec_color};">{res.reviewer_decision}</h3>
                <p style="margin: 4px 0 0; font-size: 13px; color: #334155;">
                    {'Verified without revisions needed.' if not res.revision_occurred else f'Revised {res.revision_count} time(s) based on Reviewer audit feedback.'}
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Display Final Plan
        plan_data = res.final_plan
        if plan_data:
            # Top Recommendation Highlight
            rec_title = plan_data.get("recommended_first_task_title")
            rec_reason = plan_data.get("recommended_first_reason")
            if rec_title and rec_title != "None":
                st.markdown(
                    f"""
                    <div style="background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 10px; padding: 18px; margin-bottom: 20px;">
                        <span style="font-size: 12px; font-weight: 700; color: #1d49aa; text-transform: uppercase; letter-spacing: 0.5px;">🎯 Recommended First Action</span>
                        <h3 style="margin: 4px 0 6px; color: #1e3a8a;">{rec_title}</h3>
                        <p style="margin: 0; color: #1e40af; font-size: 14px;">{rec_reason}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            st.write(f"**Workload Summary:** {plan_data.get('summary', '')}")

            # Planned Tasks List
            tasks = plan_data.get("tasks", [])
            st.subheader(f"📋 Prioritized Daily Tasks ({len(tasks)})")
            if not tasks:
                st.info("No pending tasks were scheduled.")
            else:
                for t in tasks:
                    with st.container():
                        t_c1, t_c2, t_c3 = st.columns([4, 2, 2])
                        with t_c1:
                            st.markdown(f"**{t.get('task_title')}** (`{t.get('subject')}`)")
                            st.write(f"👉 **Action:** {t.get('recommended_action')}")
                            st.caption(f"Reason: {t.get('reason_for_priority')}")
                        with t_c2:
                            st.markdown(get_priority_badge_html(t.get("priority", "NORMAL")), unsafe_allow_html=True)
                            st.write(f"Effort: **{t.get('estimated_effort')} hrs**")
                        with t_c3:
                            st.write(f"Deadline: `{t.get('deadline')}`")
                            aid = t.get("assignment_id")
                            if aid and st.button("Complete", key=f"plan_done_{aid}"):
                                database.mark_assignment_completed(int(aid))
                                st.success("Task completed!")
                                st.rerun()
                        st.divider()

            # Retry Button
            if st.button("🔄 Regenerate Plan", key="retry_plan_btn"):
                st.session_state.latest_plan_result = None
                st.rerun()


# ==============================================================================
# SECTION E: AI CHAT ASSISTANT
# ==============================================================================
elif menu_choice == "💬 AI Chat Assistant":
    st.title("💬 Academic AI Assistant")
    st.write(
        "Chat with your AI Assistant powered by Gemini. Ask questions about your deadlines, "
        "request study plans, or mark assignments completed using factual database tools."
    )

    # Initialize chat history
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = [
            {"role": "assistant", "content": "Hello! I am your Assignment Deadline Assistant. How can I help you manage your coursework today?"}
        ]

    # Quick Action Buttons
    st.write("**Quick Actions:**")
    q_col1, q_col2, q_col3, q_col4 = st.columns(4)
    with q_col1:
        if st.button("Which assignment first?", use_container_width=True):
            st.session_state.chat_messages.append({"role": "user", "content": "Which assignment should I complete first?"})
            st.rerun()
    with q_col2:
        if st.button("Show overdue tasks", use_container_width=True):
            st.session_state.chat_messages.append({"role": "user", "content": "Show my overdue assignments."})
            st.rerun()
    with q_col3:
        if st.button("Approaching deadlines?", use_container_width=True):
            st.session_state.chat_messages.append({"role": "user", "content": "Which deadlines are approaching in the next 72 hours?"})
            st.rerun()
    with q_col4:
        if st.button("Generate study plan", use_container_width=True):
            st.session_state.chat_messages.append({"role": "user", "content": "Create my daily study plan for today."})
            st.rerun()

    st.divider()

    # Display Chat History
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if "tool_traces" in msg and msg["tool_traces"]:
                with st.expander("🛠️ Tool Executions (Database Interacted)", expanded=False):
                    for trace in msg["tool_traces"]:
                        st.code(f"Tool: {trace['tool']}\nArgs: {trace['arguments']}\nResult: {trace['result']}")

    # Process pending user input
    user_input = st.chat_input("Ask a question about your assignments...")
    if user_input:
        st.session_state.chat_messages.append({"role": "user", "content": user_input})
        st.rerun()

    # If the last message was from the user, generate response
    if st.session_state.chat_messages and st.session_state.chat_messages[-1]["role"] == "user":
        api_key = config.get_gemini_api_key()
        if not api_key or api_key == "your_actual_key_here":
            with st.chat_message("assistant"):
                err_msg = "⚠️ **Gemini API Key is not configured.** Please set your API key in the **Settings** tab to enable AI chat."
                st.warning(err_msg)
                st.session_state.chat_messages.append({"role": "assistant", "content": err_msg})
        else:
            with st.chat_message("assistant"):
                with st.spinner("Thinking and querying database..."):
                    orchestrator = Orchestrator()
                    last_user_msg = st.session_state.chat_messages[-1]["content"]
                    chat_history = st.session_state.chat_messages[:-1]

                    try:
                        res = orchestrator.run_chat_assistant(
                            user_message=last_user_msg,
                            conversation_history=chat_history,
                        )
                        assistant_reply = res.get("response", "Done.")
                        traces = res.get("tool_traces", [])
                        st.markdown(assistant_reply)

                        if traces:
                            with st.expander("🛠️ Tool Executions (Database Interacted)", expanded=False):
                                for trace in traces:
                                    st.code(f"Tool: {trace['tool']}\nArgs: {trace['arguments']}\nResult: {trace['result']}")

                        st.session_state.chat_messages.append({
                            "role": "assistant",
                            "content": assistant_reply,
                            "tool_traces": traces,
                        })
                    except Exception as exc:
                        st.error(f"Error processing chat: {str(exc)}")
                        st.session_state.chat_messages.append({
                            "role": "assistant",
                            "content": f"Sorry, I encountered an error: {str(exc)}",
                        })


# ==============================================================================
# SECTION F: REMINDERS
# ==============================================================================
elif menu_choice == "⏰ Reminders":
    st.title("⏰ Deadline Reminder Center")
    st.write("Monitor background email reminder scheduling and test SMTP delivery.")

    col_smtp1, col_smtp2 = st.columns([3, 2])

    with col_smtp1:
        st.subheader("📬 Automated Reminder Delivery Status")
        records = database.get_reminder_records(limit=50)

        if not records:
            st.info("No reminder records found. Reminders are created when assignments are added.")
        else:
            for r in records[:15]:
                status_color = "#16a34a" if r.get("delivery_status") == "SENT" else ("#ea580c" if r.get("delivery_status") == "SCHEDULED" else "#dc2626")
                st.markdown(
                    f"""
                    <div style="background: white; border-radius: 8px; border: 1px solid #e2e8f0; padding: 12px; margin-bottom: 8px;">
                        <div style="display: flex; justify-content: space-between;">
                            <strong>{r.get('assignment_title', 'Assignment')}</strong>
                            <span style="color: {status_color}; font-weight: bold; font-size: 12px;">{r.get('delivery_status')}</span>
                        </div>
                        <div style="font-size: 12px; color: #64748b; margin-top: 4px;">
                            Type: <code>{r.get('reminder_type')}</code> | Scheduled: {r.get('scheduled_time')}
                            {f" | Sent: {r.get('sent_at')}" if r.get('sent_at') else ""}
                        </div>
                        {f"<div style='font-size: 11px; color: #dc2626; margin-top: 4px;'>Error: {r.get('error_message')}</div>" if r.get('error_message') else ""}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    with col_smtp2:
        st.subheader("⚙️ SMTP Configuration Status")
        smtp_cfg = reminders.get_smtp_config()
        configured = bool(smtp_cfg["email"] and smtp_cfg["password"])

        st.markdown(
            f"""
            <div style="background: white; border-radius: 10px; border: 1px solid #e2e8f0; padding: 16px;">
                <p><strong>Status:</strong> {'<span style="color: green; font-weight: bold;">Configured</span>' if configured else '<span style="color: red; font-weight: bold;">Not Configured</span>'}</p>
                <p><strong>SMTP Server:</strong> <code>{smtp_cfg['server']}:{smtp_cfg['port']}</code></p>
                <p><strong>Sender Email:</strong> <code>{smtp_cfg['email'] or 'Not set'}</code></p>
                <p><strong>Recipient Email:</strong> <code>{smtp_cfg['recipient'] or 'Not set'}</code></p>
                <p><strong>Scheduler Status:</strong> {'Active (Running)' if reminders.is_scheduler_running() else 'Inactive'}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.write("")
        st.subheader("✉️ Test Email Delivery")
        test_email_input = st.text_input("Recipient Email Address", value=smtp_cfg["recipient"])
        if st.button("Send Test Email", use_container_width=True):
            if not test_email_input:
                st.warning("Please provide a recipient email address.")
            else:
                with st.spinner("Sending test email via SMTP..."):
                    success, msg = reminders.send_test_email(test_email_input)
                    if success:
                        st.success(f"✅ {msg}")
                    else:
                        st.error(f"❌ {msg}")


# ==============================================================================
# SECTION G: SETTINGS
# ==============================================================================
elif menu_choice == "⚙️ Settings":
    st.title("⚙️ System Configuration & Diagnostics")
    st.write("Verify API connectivity, model configurations, and environment parameters.")

    st.subheader("🔑 Google Gemini API Connection Test")

    c_set1, c_set2 = st.columns([3, 2])
    with c_set1:
        st.markdown(
            f"""
            **Current Model Roles Configured:**
            - **Planner Model:** `{config.get_planner_model()}`
            - **Worker Model:** `{config.get_worker_model()}`
            - **Reviewer Model:** `{config.get_reviewer_model()}`
            """
        )

        # Allow testing with another model or custom key in UI without exposing it
        custom_key = st.text_input("Temporary Test API Key (leave empty to use .env key)", type="password")
        test_model = st.selectbox(
            "Model to test connection against",
            [config.get_planner_model(), "gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-3.8-flash"],
            index=0,
        )

        if st.button("🔌 Run Live API Connection Test", type="primary"):
            target_key = custom_key if custom_key else config.get_gemini_api_key()
            with st.spinner(f"Contacting Google Gemini API ({test_model})..."):
                success, msg, diag = config.test_api_connection(api_key=target_key, model=test_model)
                if success:
                    st.success(f"✅ {msg}")
                    st.json(diag)
                else:
                    st.error(f"❌ {msg}")
                    if diag:
                        st.json(diag)

    with c_set2:
        st.markdown(
            """
            <div style="background: white; border-radius: 10px; border: 1px solid #e2e8f0; padding: 18px;">
                <h4 style="margin-top: 0;">How to Configure .env</h4>
                <ol style="font-size: 13px; line-height: 1.6; padding-left: 20px;">
                    <li>Get a Gemini API Key from <a href="https://aistudio.google.com" target="_blank">Google AI Studio</a>.</li>
                    <li>Open or create your <code>.env</code> file in the project root.</li>
                    <li>Add your credentials:
                        <pre style="background: #f1f5f9; padding: 8px; border-radius: 4px; margin-top: 6px;">GEMINI_API_KEY=AIzaSy...
PLANNER_MODEL=gemini-2.5-flash
WORKER_MODEL=gemini-2.5-flash
REVIEWER_MODEL=gemini-2.5-flash</pre>
                    </li>
                    <li>For email notifications:
                        <pre style="background: #f1f5f9; padding: 8px; border-radius: 4px; margin-top: 6px;">SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_EMAIL=you@gmail.com
SMTP_PASSWORD=app_password</pre>
                    </li>
                </ol>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()
    st.subheader("🌐 Timezone & Regional Settings")
    st.write(f"Active Application Timezone: `{os.getenv('APP_TIMEZONE', 'UTC')}`")
    st.caption("Timezone is used for calculating overdue deadlines and time remaining deterministically.")
