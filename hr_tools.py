"""
hr_tools.py - SQLite-backed tools and agent loop for the HR Assistant.

Usage in the notebook (after the model/pipe cell):
    import hr_tools
    hr_tools.init(tokenizer, pipe, db_path="hr_assistant.db")
    print(hr_tools.run_agent("How many casual leaves do I have left?", "E101"))

Design rules
  * The model never passes the acting user's id. Tools are built per logged-in user.
  * Business rules (balances, limits, dates, ownership) are enforced in Python/SQL, not by the LLM.
  * Pulse-survey responses are stored without identity and are never written to the audit log.
"""
import os
import re
import json
import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool

# ---------------------------------------------------------------------------
# Configuration / state
# ---------------------------------------------------------------------------
DB_PATH = "hr_assistant.db"
# MUST equal the salt used in seed.py, otherwise seeded participants are not recognised as "already voted".
PULSE_SALT = os.environ.get("PULSE_SALT", "dev-only-salt")
MIN_RESPONSES = 5          # minimum responses before a pulse survey can be summarised
TODAY = None               # set to a date to freeze "today" (tests / demos with seeded data)

_tokenizer = None
_pipe = None


def init(tokenizer, pipe, db_path=None):
    """Hand the already-loaded model objects to this module."""
    global _tokenizer, _pipe, DB_PATH
    _tokenizer, _pipe = tokenizer, pipe
    if db_path:
        DB_PATH = db_path


def today():
    return TODAY or date.today()


def now_ts():
    return f"{today()} {datetime.now():%H:%M:%S}"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
@contextmanager
def db(write=False):
    """Connection with foreign keys ON. write=True wraps everything in one IMMEDIATE transaction."""
    con = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        if write:
            con.execute("BEGIN IMMEDIATE")
        yield con
        if write:
            con.execute("COMMIT")
    except BaseException:
        if write and con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def j(obj):
    return json.dumps(obj, default=str)


def _err(msg):
    return json.dumps({"error": msg})


def _d(s):
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def business_days(start, end):
    n, d = 0, start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def _next_ticket_number(con):
    # one shared counter across LV- and RB- tickets; safe because callers hold BEGIN IMMEDIATE
    return con.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(ticket_id, 4) AS INTEGER)), 1000) + 1 FROM tickets"
    ).fetchone()[0]


def get_user(uid):
    with db() as con:
        row = con.execute("SELECT emp_id, name, role, status FROM employees WHERE emp_id = ?", (uid,)).fetchone()
    return dict(row) if row else None


def _role(con, uid):
    r = con.execute("SELECT role FROM employees WHERE emp_id = ?", (uid,)).fetchone()
    return r["role"] if r else None


def _can_view(con, uid, target):
    """Self, the person's direct manager, or HR."""
    if target == uid or _role(con, uid) == "hr":
        return True
    t = con.execute("SELECT manager_id FROM employees WHERE emp_id = ?", (target,)).fetchone()
    return bool(t and t["manager_id"] == uid)


# ---------------------------------------------------------------------------
# LLM helper (used by feedback writing and pulse summaries)
# ---------------------------------------------------------------------------
def llm_generate(system, user, max_new_tokens=600):
    if _pipe is None or _tokenizer is None:
        raise RuntimeError("Call hr_tools.init(tokenizer, pipe) first")
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    prompt = _tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    return _pipe(prompt, max_new_tokens=max_new_tokens)[0]["generated_text"].strip()


FEEDBACK_SYSTEM = """You are an HR performance-review writing assistant for managers.
Rewrite the manager's rough notes into constructive, professional appraisal feedback.
Rules:
- Use only facts in the notes. Do not invent achievements, numbers or incidents.
- Describe behaviour and impact (Situation-Behaviour-Impact), never personality traits.
- Never mention or infer age, gender, religion, caste, health, family status or other protected attributes.
- Do not give a numeric rating, promotion or salary recommendation.
Output exactly these sections:
**Strengths** (2-3 bullets)
**Areas for improvement** (2-3 bullets, each with a concrete suggestion)
**Suggested OKRs for next quarter** (2-3, each with an Objective and measurable Key Results)"""

PULSE_SYSTEM = """You summarize anonymous employee pulse-survey responses for HR.
Rules:
- Never quote text that could identify a person, team or incident. Paraphrase only.
- Do not guess who wrote anything.
- Output: **Overall sentiment** (positive/mixed/negative, one line), **Top themes** (3-5 bullets with approximate share
  such as 'about half'), **Suggested actions** (2-3 bullets)."""


# ---------------------------------------------------------------------------
# Employee tools: leave, reimbursement, tickets, onboarding, pulse, escalation
# ---------------------------------------------------------------------------
def make_employee_tools(uid):
    @tool
    def get_leave_balance() -> str:
        """Get the current user's leave balance for casual, sick and earned leave, including days already
        reserved by pending requests and the days still available to apply for."""
        with db() as con:
            rows = con.execute(
                """SELECT b.leave_type, b.balance, COALESCE(r.reserved_days, 0) AS pending
                   FROM leave_balances b
                   LEFT JOIN v_leave_reserved r ON r.emp_id = b.emp_id AND r.leave_type = b.leave_type
                   WHERE b.emp_id = ?""", (uid,)).fetchall()
        return j({r["leave_type"]: {"balance": r["balance"], "pending_requests": r["pending"],
                                    "available": r["balance"] - r["pending"]} for r in rows})

    @tool
    def apply_leave(leave_type: str, start_date: str, end_date: str, reason: str = "") -> str:
        """Apply for leave. leave_type is casual, sick or earned. Dates must be YYYY-MM-DD (inclusive)."""
        leave_type = leave_type.lower().strip()
        try:
            s, e = _d(start_date), _d(end_date)
        except ValueError:
            return _err("Dates must be in YYYY-MM-DD format")
        if e < s:
            return _err("end_date is before start_date")
        with db(write=True) as con:
            lt = con.execute("SELECT backdate_allowed FROM leave_types WHERE leave_type = ?", (leave_type,)).fetchone()
            if not lt:
                types = [r[0] for r in con.execute("SELECT leave_type FROM leave_types")]
                return _err(f"leave_type must be one of {types}")
            if not con.execute("SELECT manager_id FROM employees WHERE emp_id = ?", (uid,)).fetchone()["manager_id"]:
                return _err("No manager is on record to approve this. Please contact HR directly.")
            if s < today() and not lt["backdate_allowed"]:
                return _err("Cannot apply for past dates (only leave types that allow backdating, like sick leave)")
            days = business_days(s, e)
            if days == 0:
                return _err("Selected dates are all weekends")
            clash = con.execute(
                """SELECT ticket_id FROM tickets WHERE emp_id = ? AND kind = 'leave'
                   AND status IN ('pending', 'approved') AND start_date <= ? AND end_date >= ?""",
                (uid, end_date.strip(), start_date.strip())).fetchone()
            if clash:
                return _err(f"These dates overlap with your existing request {clash[0]}")
            bal = con.execute("SELECT balance FROM leave_balances WHERE emp_id = ? AND leave_type = ?",
                              (uid, leave_type)).fetchone()
            reserved = con.execute(
                "SELECT COALESCE(SUM(days), 0) FROM tickets WHERE emp_id = ? AND kind = 'leave' AND type = ? AND status = 'pending'",
                (uid, leave_type)).fetchone()[0]
            available = (bal["balance"] if bal else 0) - reserved
            if days > available:
                return _err(f"Insufficient {leave_type} leave: need {days}, available {available}")
            tid = f"LV-{_next_ticket_number(con)}"
            con.execute(
                """INSERT INTO tickets(ticket_id, kind, emp_id, type, start_date, end_date, days, reason, created_at)
                   VALUES (?, 'leave', ?, ?, ?, ?, ?, ?, ?)""",
                (tid, uid, leave_type, start_date.strip(), end_date.strip(), days, reason, now_ts()))
        return j({"ok": True, "ticket_id": tid, "days": days, "status": "pending manager approval"})

    @tool
    def submit_reimbursement(category: str, amount: float, description: str) -> str:
        """Submit a reimbursement claim. category is travel, meals, internet or medical. amount is in INR."""
        category = category.lower().strip()
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return _err("amount must be a number")
        with db(write=True) as con:
            lim = con.execute("SELECT limit_inr FROM reimbursement_limits WHERE category = ?", (category,)).fetchone()
            if not lim:
                cats = [r[0] for r in con.execute("SELECT category FROM reimbursement_limits")]
                return _err(f"category must be one of {cats}")
            if amount <= 0:
                return _err("amount must be positive")
            if amount > lim["limit_inr"]:
                return _err(f"Exceeds the {category} limit of INR {lim['limit_inr']:.0f}. Needs an HR exception.")
            if not con.execute("SELECT manager_id FROM employees WHERE emp_id = ?", (uid,)).fetchone()["manager_id"]:
                return _err("No manager is on record to approve this. Please contact HR directly.")
            tid = f"RB-{_next_ticket_number(con)}"
            con.execute(
                """INSERT INTO tickets(ticket_id, kind, emp_id, type, amount, description, created_at)
                   VALUES (?, 'reimbursement', ?, ?, ?, ?, ?)""",
                (tid, uid, category, amount, description, now_ts()))
        return j({"ok": True, "ticket_id": tid, "status": "pending manager approval"})

    @tool
    def check_ticket_status(ticket_id: str) -> str:
        """Check the status of one of the current user's leave or reimbursement tickets, e.g. LV-1001."""
        with db() as con:
            r = con.execute("SELECT * FROM tickets WHERE ticket_id = ? AND emp_id = ?",
                            (ticket_id.strip().upper(), uid)).fetchone()
        return j(dict(r)) if r else _err("Ticket not found")      # same message for 'missing' and 'not yours'

    @tool
    def list_my_tickets() -> str:
        """List the current user's most recent leave and reimbursement tickets."""
        with db() as con:
            rows = con.execute(
                """SELECT ticket_id, kind, type, status, start_date, end_date, days, amount, manager_comment
                   FROM tickets WHERE emp_id = ? ORDER BY created_at DESC, ticket_id DESC LIMIT 20""",
                (uid,)).fetchall()
        return j([dict(r) for r in rows])

    @tool
    def get_onboarding_tasks(emp_id: str = "", pending_only: bool = True) -> str:
        """List onboarding/offboarding checklist tasks. Leave emp_id empty for the current user's own tasks.
        Managers can look up direct reports; HR can look up anyone. Set pending_only=false to include completed tasks."""
        target = (emp_id or uid).strip().upper()
        with db() as con:
            if not _can_view(con, uid, target):
                return _err("Employee not found")
            q = "SELECT task_id, lifecycle, phase, task, done, completed_at FROM lifecycle_tasks WHERE emp_id = ?"
            if pending_only:
                q += " AND done = 0"
            rows = con.execute(q + " ORDER BY lifecycle, task_id", (target,)).fetchall()
        return j({"emp_id": target, "tasks": [dict(r) for r in rows]})

    @tool
    def mark_task_complete(task_id: str, emp_id: str = "") -> str:
        """Mark an onboarding task (e.g. T2) as completed. Employees can complete their own onboarding tasks;
        offboarding tasks and other people's tasks can only be completed by HR."""
        target = (emp_id or uid).strip().upper()
        task_id = task_id.strip().upper()
        with db(write=True) as con:
            role = _role(con, uid)
            if target != uid and role != "hr":
                return _err("Task not found")
            t = con.execute("SELECT lifecycle, task, done FROM lifecycle_tasks WHERE emp_id = ? AND task_id = ?",
                            (target, task_id)).fetchone()
            if not t:
                return _err("Task not found")
            if t["lifecycle"] == "offboarding" and role != "hr":
                return _err("Offboarding tasks can only be completed by HR")
            if t["done"]:
                return _err("Task is already completed")
            con.execute("UPDATE lifecycle_tasks SET done = 1, completed_at = ? WHERE emp_id = ? AND task_id = ?",
                        (now_ts(), target, task_id))
        return j({"ok": True, "completed": t["task"]})

    @tool
    def submit_pulse_feedback(survey_id: str, feedback: str) -> str:
        """Submit anonymous feedback for a pulse survey. The response is stored without the user's identity."""
        survey_id = survey_id.strip()
        feedback = feedback.strip()
        if not feedback or len(feedback) > 1000:
            return _err("Feedback must be between 1 and 1000 characters")
        digest = hashlib.sha256((PULSE_SALT + uid).encode()).hexdigest()
        iso = today().isocalendar()
        with db(write=True) as con:
            sv = con.execute("SELECT status, opens_on, closes_on FROM pulse_surveys WHERE survey_id = ?",
                             (survey_id,)).fetchone()
            if not sv:
                return _err("Survey not found")
            if sv["status"] != "open" or not (sv["opens_on"] <= str(today()) <= sv["closes_on"]):
                return _err("This survey is not open for responses")
            try:
                con.execute("INSERT INTO pulse_participants(survey_id, participant_hash) VALUES (?, ?)",
                            (survey_id, digest))
            except sqlite3.IntegrityError:
                return _err("You have already responded to this survey")
            names = [r[0] for r in con.execute("SELECT name FROM employees")]
            con.execute("INSERT INTO pulse_responses(survey_id, response_text, submitted_week) VALUES (?, ?, ?)",
                        (survey_id, scrub_pii(feedback, names), f"{iso[0]}-W{iso[1]:02d}"))
        return j({"ok": True, "message": "Thanks, your response was recorded anonymously"})

    @tool
    def escalate_to_hr(category: str, summary: str) -> str:
        """Escalate a sensitive matter to a human HR representative. category is posh, disciplinary, legal or
        other_sensitive. summary is ONE short neutral sentence; do not repeat accusations or details."""
        category = category.lower().strip()
        if category not in ("posh", "disciplinary", "legal", "other_sensitive"):
            category = "other_sensitive"
        with db(write=True) as con:
            cur = con.execute("INSERT INTO escalations(emp_id, category, summary, created_at) VALUES (?, ?, ?, ?)",
                              (uid, category, summary[:300], now_ts()))
        return j({"ok": True, "escalation_id": cur.lastrowid, "message": "Escalated to a human HR representative"})

    return [get_leave_balance, apply_leave, submit_reimbursement, check_ticket_status, list_my_tickets,
            get_onboarding_tasks, mark_task_complete, submit_pulse_feedback, escalate_to_hr]


# ---------------------------------------------------------------------------
# Pulse helpers
# ---------------------------------------------------------------------------
def scrub_pii(text, names=()):
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[EMAIL]", text)
    text = re.sub(r"\+?\d[\d\s-]{8,}\d", "[PHONE]", text)
    for full in names:
        for part in full.split():
            if len(part) >= 3:
                text = re.sub(rf"\b{re.escape(part)}\b", "[NAME]", text, flags=re.I)
    return text


def make_pulse_summary_tool():
    @tool
    def summarize_pulse(survey_id: str) -> str:
        """Summarize recurring themes and sentiment of an anonymous company-wide pulse survey."""
        survey_id = survey_id.strip()
        with db() as con:
            rows = con.execute("SELECT response_text FROM pulse_responses WHERE survey_id = ? ORDER BY RANDOM()",
                               (survey_id,)).fetchall()
            names = [r[0] for r in con.execute("SELECT name FROM employees")]
        if len(rows) < MIN_RESPONSES:
            return _err(f"Only {len(rows)} responses. A minimum of {MIN_RESPONSES} is needed to protect anonymity.")
        joined = "\n".join(f"- {scrub_pii(r['response_text'], names)}" for r in rows)
        return j({"responses": len(rows), "summary": llm_generate(PULSE_SYSTEM, f"{len(rows)} responses:\n{joined}")})
    return summarize_pulse


# ---------------------------------------------------------------------------
# Manager tools
# ---------------------------------------------------------------------------
def make_manager_tools(uid):
    @tool
    def get_team_pending_requests() -> str:
        """List pending leave and reimbursement tickets from the manager's direct reports."""
        with db() as con:
            rows = con.execute("SELECT * FROM v_pending_by_manager WHERE manager_id = ? ORDER BY created_at",
                               (uid,)).fetchall()
        return j([dict(r) for r in rows])

    @tool
    def decide_request(ticket_id: str, decision: str, comment: str = "") -> str:
        """Approve or reject a direct report's pending ticket. decision is 'approved' or 'rejected'."""
        decision = decision.lower().strip()
        if decision not in ("approved", "rejected"):
            return _err("decision must be 'approved' or 'rejected'")
        tid = ticket_id.strip().upper()
        with db(write=True) as con:
            t = con.execute(
                """SELECT t.*, e.manager_id FROM tickets t JOIN employees e ON e.emp_id = t.emp_id
                   WHERE t.ticket_id = ?""", (tid,)).fetchone()
            if not t or t["manager_id"] != uid:
                return _err("Ticket not found among your direct reports")
            if t["status"] != "pending":
                return _err(f"Ticket already {t['status']}")
            if decision == "approved" and t["kind"] == "leave":
                bal = con.execute("SELECT balance FROM leave_balances WHERE emp_id = ? AND leave_type = ?",
                                  (t["emp_id"], t["type"])).fetchone()
                if not bal or bal["balance"] < t["days"]:
                    return _err("Employee no longer has enough leave balance to approve this")
                con.execute("UPDATE leave_balances SET balance = balance - ? WHERE emp_id = ? AND leave_type = ?",
                            (t["days"], t["emp_id"], t["type"]))
            con.execute("UPDATE tickets SET status = ?, manager_comment = ?, decided_by = ?, decided_at = ? WHERE ticket_id = ?",
                        (decision, comment, uid, now_ts(), tid))
        return j({"ok": True, "ticket_id": tid, "status": decision})

    @tool
    def write_appraisal_feedback(employee_id: str, rough_notes: str, review_cycle: str = "") -> str:
        """Turn a manager's rough bullet points about a direct report into appraisal feedback and OKR suggestions.
        Leave review_cycle empty to use the current half-year."""
        emp = employee_id.strip().upper()
        cycle = review_cycle.strip() or f"H{1 if today().month <= 6 else 2}-{today().year}"
        with db() as con:
            r = con.execute("SELECT name FROM employees WHERE emp_id = ? AND manager_id = ?", (emp, uid)).fetchone()
        if not r:
            return _err("You can only write feedback for your direct reports")
        text = llm_generate(FEEDBACK_SYSTEM, f"Employee: {r['name']}\nManager's rough notes:\n{rough_notes}")
        with db(write=True) as con:
            cur = con.execute(
                """INSERT INTO performance_feedback(employee_id, manager_id, review_cycle, rough_notes,
                                                    generated_feedback, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                (emp, uid, cycle, rough_notes, text, now_ts()))
        return j({"feedback_id": cur.lastrowid, "review_cycle": cycle, "feedback": text})

    return [get_team_pending_requests, decide_request, write_appraisal_feedback]


# ---------------------------------------------------------------------------
# HR tools: offboarding and letters
# ---------------------------------------------------------------------------
def make_hr_tools(uid):
    @tool
    def start_offboarding(emp_id: str, last_working_day: str) -> str:
        """Start offboarding for an employee and create their checklist. last_working_day must be YYYY-MM-DD."""
        emp = emp_id.strip().upper()
        try:
            lwd = _d(last_working_day)
        except ValueError:
            return _err("last_working_day must be in YYYY-MM-DD format")
        with db(write=True) as con:
            e = con.execute("SELECT status FROM employees WHERE emp_id = ?", (emp,)).fetchone()
            if not e:
                return _err("Employee not found")
            if e["status"] in ("offboarding", "exited"):
                return _err(f"Employee is already {e['status']}")
            if lwd < today():
                return _err("last_working_day cannot be in the past")
            con.execute("UPDATE employees SET status = 'offboarding', last_working_day = ? WHERE emp_id = ?",
                        (str(lwd), emp))
            tpl = con.execute("SELECT phase, task FROM task_templates WHERE lifecycle = 'offboarding' "
                              "ORDER BY sort_order, template_id").fetchall()
            for i, t in enumerate(tpl, 1):
                con.execute("INSERT INTO lifecycle_tasks(task_id, emp_id, lifecycle, phase, task) "
                            "VALUES (?, ?, 'offboarding', ?, ?)", (f"X{i}", emp, t["phase"], t["task"]))
        return j({"ok": True, "emp_id": emp, "last_working_day": str(lwd), "tasks": [t["task"] for t in tpl]})

    @tool
    def draft_hr_letter(letter_type: str, emp_id: str, end_date: str = "") -> str:
        """Draft an HR letter from a template and save it. letter_type is 'welcome_email' or 'experience_letter'.
        Experience letters need an offboarding or exited employee; end_date defaults to their last working day."""
        emp = emp_id.strip().upper()
        letter_type = letter_type.strip().lower()
        with db(write=True) as con:
            tpl = con.execute("SELECT body FROM letter_templates WHERE letter_type = ?", (letter_type,)).fetchone()
            if not tpl:
                types = [r[0] for r in con.execute("SELECT letter_type FROM letter_templates")]
                return _err(f"Unknown letter type. Use one of {types}")
            e = con.execute(
                """SELECT e.name, e.job_title AS role, d.name AS dept, e.join_date, m.name AS manager,
                          e.status, e.last_working_day
                   FROM employees e JOIN departments d ON d.dept_id = e.dept_id
                   LEFT JOIN employees m ON m.emp_id = e.manager_id WHERE e.emp_id = ?""", (emp,)).fetchone()
            if not e:
                return _err("Employee not found")
            end = end_date.strip() or e["last_working_day"] or ""
            if letter_type == "experience_letter":
                if e["status"] not in ("offboarding", "exited"):
                    return _err("Experience letters are only for offboarding or exited employees")
                if not end:
                    return _err("end_date is required (no last working day on record)")
            try:
                content = tpl["body"].format(name=e["name"], role=e["role"], dept=e["dept"],
                                             join_date=e["join_date"], manager=e["manager"] or "HR",
                                             end_date=end or str(today()))
            except (KeyError, IndexError) as ex:
                return _err(f"Template uses an unknown field: {ex}")
            cur = con.execute(
                "INSERT INTO generated_letters(emp_id, letter_type, content, requested_by, created_at) "
                "VALUES (?, ?, ?, ?, ?)", (emp, letter_type, content, uid, now_ts()))
        return j({"letter_id": cur.lastrowid, "letter": content})

    return [start_offboarding, draft_hr_letter]


# ---------------------------------------------------------------------------
# Role-based tool sets
# ---------------------------------------------------------------------------
def build_tools_for(uid):
    role = (get_user(uid) or {}).get("role")
    tools = make_employee_tools(uid)
    if role == "manager":
        tools += make_manager_tools(uid) + [make_pulse_summary_tool()]
    elif role == "hr":
        tools += make_hr_tools(uid) + [make_pulse_summary_tool()]
    return tools


# ---------------------------------------------------------------------------
# Sensitive-topic guard (deterministic, runs BEFORE the LLM)
# ---------------------------------------------------------------------------
SENSITIVE_PATTERNS = [
    ("posh", r"harass|\bposh\b|molest|assault|sexual|stalk|inappropriate(ly)? touch"),
    ("disciplinary", r"disciplinary|show[- ]cause|misconduct|suspension|terminat(ed|ion) notice"),
    ("legal", r"lawsuit|legal (action|notice|dispute)|\bsue\b|\bcourt\b|lawyer|advocate"),
]


def check_sensitive(text):
    low = text.lower()
    for category, pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, low):
            return category
    return None


# ---------------------------------------------------------------------------
# Tool execution with audit logging
# ---------------------------------------------------------------------------
NO_AUDIT = {"submit_pulse_feedback"}     # logging who submitted when would weaken pulse anonymity


def _audit(uid, name, args, result):
    if name in NO_AUDIT:
        return
    try:
        outcome = "ok"
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and "error" in parsed:
                outcome = "denied" if "not found" in parsed["error"].lower() or "only" in parsed["error"].lower() else "error"
        except (ValueError, TypeError):
            pass
        short = {k: (str(v)[:200]) for k, v in (args or {}).items()}
        with db(write=True) as con:
            con.execute("INSERT INTO audit_log(actor_emp_id, tool_name, arguments, outcome, created_at) VALUES (?, ?, ?, ?, ?)",
                        (uid, name, json.dumps(short), outcome, now_ts()))
    except Exception:
        pass                              # auditing must never break the user's request


def execute_tool(uid, tool_map, name, args):
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    args = args or {}
    if name not in tool_map:
        result = _err("Unknown or unauthorized tool")
        try:
            with db(write=True) as con:
                con.execute("INSERT INTO audit_log(actor_emp_id, tool_name, arguments, outcome, created_at) "
                            "VALUES (?, ?, ?, 'denied', ?)", (uid, str(name)[:100], json.dumps(args)[:500], now_ts()))
        except Exception:
            pass
        return result
    try:
        result = str(tool_map[name].invoke(args))
    except Exception as e:
        result = _err(f"Tool failed: {e}")
    _audit(uid, name, args, result)
    return result


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def run_agent(user_msg, uid, max_steps=5, verbose=True):
    user = get_user(uid)
    if not user:
        return "Unknown employee id."
    if user["status"] == "exited":
        return "Your access has been disabled. Please contact HR."

    category = check_sensitive(user_msg)
    if category:
        with db(write=True) as con:
            cur = con.execute("INSERT INTO escalations(emp_id, category, summary, created_at) VALUES (?, ?, ?, ?)",
                              (uid, category, "Employee raised a sensitive matter via the HR assistant; details not handled in chat.",
                               now_ts()))
        if verbose:
            print(f"🚨 [{uid}] escalated ({category}) #{cur.lastrowid}")
        return ("This is something I can't handle in chat. I've passed it to a human HR representative "
                f"(reference #{cur.lastrowid}), who will follow up with you.")

    tools = build_tools_for(uid)
    tool_map = {t.name: t for t in tools}
    schemas = [convert_to_openai_tool(t) for t in tools]
    system = f"""You are an HR Self-Service Assistant. Today is {today()} ({today():%A}).
You are talking to {user['name']} (role: {user['role']}).
- Use tools for every data lookup or action. Never invent balances, tickets, statuses or letters.
- Convert relative dates ('tomorrow', 'next Monday') into YYYY-MM-DD before calling tools.
- If a tool returns an error, explain it to the user in plain words.
- For harassment/POSH, disciplinary or legal matters, call escalate_to_hr and tell the user a human HR representative will follow up."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]

    for _ in range(max_steps):
        prompt = _tokenizer.apply_chat_template(messages, tools=schemas, tokenize=False, add_generation_prompt=True)
        out = _pipe(prompt, max_new_tokens=700)[0]["generated_text"].strip()
        calls = []
        for raw in TOOL_CALL_RE.findall(out):
            try:
                calls.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
        if not calls:
            return out
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"type": "function", "function": c} for c in calls]})
        for c in calls:
            name, args = c.get("name"), c.get("arguments", {})
            if verbose:
                print(f"🔧 [{uid}] {name}({args})")
            messages.append({"role": "tool", "name": name, "content": execute_tool(uid, tool_map, name, args)})
    return "Stopped: too many steps."
