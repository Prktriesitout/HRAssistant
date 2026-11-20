```markdown
# 🤖 AI-Powered HR Assistant Platform

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![LangChain](https://img.shields.io/badge/Framework-LangChain-orange.svg)](https://github.com/langchain-ai/langchain)
[![Database](https://img.shields.io/badge/Database-SQLite-lightgrey.svg)](https://www.sqlite.org/)

An advanced, LLM-powered Human Resources Assistant capable of handling leave requests, submitting reimbursements, checking tickets, managing onboarding tasks, collecting anonymous pulse feedback, and conducting secure, role-based operations (RBAC).

---

## 🛠️ System Architecture

```
                     +------------------------------+
                     |      User/Employee Chat      |
                     +--------------+---------------+
                                    |
                                    v
                     +--------------+---------------+
                     |       Keyword Guard &        |
                     |     Sensitive Escalation     | ----> [ Escalations Table ]
                     +--------------+---------------+
                                    |
                                    v
                     +--------------+---------------+
                     |    LangChain Agent (LLM)     |
                     +--------------+---------------+
                                    |
         +--------------------------+--------------------------+
         |                          |                          |
         v                          v                          v
+--------+--------+       +---------+--------+       +---------+--------+
|   Leave Tools   |       |  Reimbursement   |       | Onboarding Tasks |
|  & Entitlements |       |      Claims      |       |  & Lifecycle     |
+-----------------+       +------------------+       +------------------+
```

---

## ✨ Core Features

*   **📅 Intelligent Leave Management**: Check entitlements, apply for leaves, and track approval states with dynamic manager-employee relationship awareness.
*   **💸 Reimbursement Processing**: Submit claims with automatic category boundaries and validation logic.
*   **📝 Automated Ticketing & Onboarding**: Complete lifecycle checklists, query team-level tasks, and draft HR documents (welcome emails, experience letters) instantly.
*   **📊 Anonymous Pulse Surveys**: Submit survey answers securely protected by SHA-256 salted hashes, preventing linkage back to employees while enabling aggregate reporting.
*   **🛡️ Multi-Tier Security (RBAC)**: Enforces role-based action controls ensuring only direct managers or specialized roles (HR, Admin) can trigger specific updates or draft credentials.
*   **🚨 Automatic Escalation Guard**: Built-in regex monitoring flags high-risk or sensitive messages (e.g., harassment, legal action) and creates database escalations immediately, completely bypassing the LLM.

---

## 📂 Database Schema Overview

This application relies on a robust SQLite relational model (`hr_assistant.db`) built with critical performance and data integrity constraints:

| Table | Description | Key Constraints / Design |
| :--- | :--- | :--- |
| **`employees`** | Directory of staff details | Unique `emp_id`, Role, Status, Manager ID |
| **`leave_balances`** | Allocation and usage of leave types | Linked per-employee with distinct buckets |
| **`tickets`** | Tickets representing active requests | Non-null `emp_id`, track request details |
| **`lifecycle_tasks`** | Onboarding or offboarding checklists | Composite Primary Key `(emp_id, task_id)` |
| **`pulse_participants`** | Survey response tracking | Stores `participant_hash` to guarantee anonymity |
| **`pulse_responses`** | Anonymous survey feedback | Disconnected from direct employee keys |
| **`escalations`** | Flagged sensitive scenarios | Auto-logged with default pending status |

---

## 🚀 Quick Start Guide

### 1. Installation
Ensure your target Python environment has the necessary requirements:
```bash
pip install langchain-core transformers torch sqlite3
```

### 2. Initialization & Configuration
Import the HR toolset and configure the credentials inside your environment:
```python
import hr_tools
from datetime import date

# Configure DB & Context
hr_tools.init(tokenizer, pipeline_object, db_path="hr_assistant.db")
hr_tools.PULSE_SALT = "YOUR_SECURE_SALT_KEY" 
hr_tools.TODAY = date(2026, 11, 16)
```

### 3. Interacting with the Assistant
Run the agent securely using a specified Employee ID to enforce proper access control boundaries:
```python
# Aaorav (Employee) checking leave balance
response = hr_tools.run_agent("How many casual leaves do I have left?", uid="E101")
print(response)

# Manager approving pending requests for their team
manager_response = hr_tools.run_agent("Show pending requests from my team.", uid="E200")
print(manager_response)
```

---

## 🔒 Security & Audit Guidelines

*   **Audit Logging**: Every API tool invocation is recorded under the `audit_log` system to ensure absolute transparency and compliance.
*   **Anonymity Policy**: Pulse survey responses utilize a irreversible combination of `PULSE_SALT + emp_id` to generate `participant_hash`. Pulse submissions are strictly excluded from the `audit_log` tool list to keep the source untraceable.
*   **RBAC Policy Matrix**:
    *   **Employee**: Read-only access to personal tickets, leave balances, and lifecycle tasks.
    *   **Manager**: Query reports, decide on pending requests for direct reports, and view team dashboards.
    *   **HR**: Run offboarding processes, draft formal letters, and access aggregated survey data.
```
